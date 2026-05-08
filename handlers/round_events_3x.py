"""WebSocket round event handlers."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

import cache.schemas as cache_schemas
from cache import room_cache
from cache.room_state_manager import RoundStateManager
from cache.room_state_manager import RoomStateManager
from client_manager import ClientManager, Client
from db import crud
from db import models
from db.session import session_scope
from handlers.audio_common import preload_songs_for_round_start
from handlers.registe_manager import regist
from handlers.round_state_events import handle_round_state_transition
from schemas.ws_messages.round_event_schemas import (
    AnswerBroadcastData,
    AnswerBroadcastMessage,
    AnswerQueueData,
    AnswerQueueMessage,
    AttemptAnswerMessage,
    GameStartMessage,
    RoundEndMessage,
    RoundStartData,
    RoundStartMessage,
    SubmitAnswerMessage,
    YourTurnData,
    YourTurnMessage,
)
from schemas.ws_messages.playback_schemas import (
    PlayControlData,
    PlayMessage,
    PauseMessage,
)
from schemas.ws_messages.judge_schemas import SkipRoundMessage

from utils import get_audio_stream_url, get_logger
from utils.enumerations import ErrorEventType, GameEventType, RoundState
from utils.ts import get_ts_ms

logger = get_logger(__name__)


@regist(GameEventType.GAME_START, data_validator=GameStartMessage)
async def handle_game_start(  # pylint: disable=too-many-locals
    data: GameStartMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    """Handle GAME_START event: initialize game round."""
    logger.info("Handling game start event for room %s", room_id)

    async with session_scope() as session:
        room = (await
                session.execute(select(models.Room).where(models.Room.id == room_id)
                                )).scalar_one_or_none()

        if not room:
            logger.error("Room %s not found", room_id)
            await client.send_error(GameEventType.GAME_START, "Room not found")
            return

        song_queue = await crud.get_room_song_queue(session, room_id)
        if not song_queue:
            logger.warning("Room %s has no songs when starting game", room_id)
            await clients.broadcast_error(
                room_id,
                ErrorEventType.HANDLER_EXCEPTION.value,
                "Cannot start game: no songs in room",
            )
            return

        # 开始游戏前校验：队列前3首（不足3首则按实际数量）歌曲必须已下载完成（基于 tasks 表）
        required_precheck_count = min(3, len(song_queue))
        precheck_song_ids = song_queue[:required_precheck_count]
        if required_precheck_count < 3:
            logger.info(
                "Room %s has only %d songs in queue, precheck will validate all available songs",
                room_id,
                required_precheck_count,
            )

        if precheck_song_ids:
            songs_stmt = select(models.Song).where(
                models.Song.id.in_(precheck_song_ids))
            songs_res = await session.execute(songs_stmt)
            song_map = {song.id: song for song in songs_res.scalars().all()}

            not_ready_song_ids: list[int] = []
            for song_id in precheck_song_ids:
                song = song_map.get(song_id)
                if not song:
                    not_ready_song_ids.append(song_id)
                    continue

                # 仅对有平台歌曲ID的歌曲执行下载任务状态校验
                if not song.platform_song_id:
                    continue

                task_id = f"download_and_cache_song:{song.platform_song_id}"
                task_record = await crud.get_task_record_by_task_id(session, task_id)
                if not task_record or task_record.status != "success":
                    not_ready_song_ids.append(song_id)

            if not_ready_song_ids:
                logger.warning(
                    "Room %s cannot start game: first %d songs not downloaded, song_ids=%s",
                    room_id,
                    required_precheck_count,
                    not_ready_song_ids,
                )
                await client.send_error(
                    GameEventType.GAME_START,
                    f"Cannot start game: first {required_precheck_count} songs not downloaded",
                )
                return

        # 使用 RoomStateManager 处理游戏开始
        if not await RoomStateManager.start_game(room_id, session):
            await client.send_error(GameEventType.GAME_START, "Failed to start game")
            return

        room.show_answer = False

        # 广播游戏开始消息
        await clients.broadcast(room_id, data.model_dump())

        # 生成临时音频访问令牌（与后续轮次统一走CRUD，确保DB/Redis一致）
        first_song_id = song_queue[0]
        audio_token = await crud.get_or_create_audio_token(session, room_id,
                                                           first_song_id)
        audio_url = get_audio_stream_url(audio_token)

        # 触发预下载（i=0）：下载 i+3 歌曲
        await preload_songs_for_round_start(
            clients=clients,
            session=session,
            room_id=room_id,
            song_queue=song_queue,
            current_index=0,
        )

        # 立即发送第一个回合开始事件
        await crud.update_room_current_song_index(session, room_id, 0)
        playback_state = cache_schemas.PlaybackState(
            play_state="playing",
            progress_ms=0,
            offset_ts=get_ts_ms(),
            audio_url=audio_url,
        )

        # 触发状态转换到 PLAYING_AUDIO（并广播）
        await handle_round_state_transition(clients, client, room_id,
                                            RoundState.PLAYING_AUDIO)

        tasks = [
            clients.broadcast(
                room_id,
                RoundStartMessage(data=RoundStartData(round_index=0,
                                                      audio_url=audio_url,
                                                      start_percent=0.0)).model_dump(),
            ),
            room_cache.set_room_playback_state(room_id, playback_state),
        ]
        # 使用 asyncio.gather 来并行执行广播和数据库提交
        res = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(res):
            if isinstance(r, Exception):
                logger.error(
                    "Exception occurred in asyncio.gather task %d for GAME_START "
                    "event in room %s: %s",
                    i,
                    room_id,
                    r,
                    exc_info=r,
                )


@regist(GameEventType.ROUND_END, data_validator=RoundEndMessage)
async def handle_round_end(
    data: RoundEndMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    """Handle ROUND_END event: manually end current round."""
    logger.info("Handling round end event for room %s", room_id)
    if not client.user.is_owner:
        logger.warning(
            "User %s attempted to end round in room %s but is not the owner",
            client.user.username,
            room_id,
        )
        await client.send_error(GameEventType.ROUND_END,
                                "Only the room owner can end the round manually")
        return

    await handle_round_state_transition(clients, client, room_id, RoundState.COMPLETED)
    await clients.broadcast(room_id, data.model_dump())


@regist(GameEventType.SKIP_ROUND, data_validator=SkipRoundMessage)
async def handle_skip_round(
    data: SkipRoundMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **kwargs: Any,
) -> None:
    """Handle SKIP_ROUND event: maintain playlist index and start next round."""
    # pylint: disable=unused-argument,too-many-return-statements
    logger.info("Handling skip round event for room %s", room_id)

    if not client.user.is_owner:
        logger.warning(
            "User %s attempted to skip round in room %s but is not the owner",
            client.user.username,
            room_id,
        )
        await client.send_error(GameEventType.SKIP_ROUND,
                                "Only the room owner can skip the round")
        return

    async with session_scope() as session:
        room = (await
                session.execute(select(models.Room).where(models.Room.id == room_id)
                                )).scalar_one_or_none()

        if not room:
            logger.error("Room %s not found when skipping round", room_id)
            await client.send_error(GameEventType.SKIP_ROUND, "Room not found")
            return

        song_queue = await crud.get_room_song_queue(session, room_id)
        if not song_queue:
            logger.warning("Room %s has no songs when skipping round", room_id)
            await client.send_error(GameEventType.SKIP_ROUND,
                                    "Cannot skip round: no songs in room")
            return

        current_index = room.current_song_index or 0
        next_index = current_index + 1
        if next_index >= len(song_queue):
            logger.warning(
                "No more songs available after skip in room %s (current=%d, total=%d)",
                room_id,
                current_index,
                len(song_queue),
            )
            await client.send_error(GameEventType.SKIP_ROUND,
                                    "Cannot skip round: already at last song")
            return

        # 1) 优先维护播放列表：推进当前歌曲索引
        await crud.update_room_current_song_index(session, room_id, next_index)
        room.show_answer = False

        # 清理上一轮的作答状态
        await room_cache.clear_answer_queue(room_id)
        await room_cache.clear_room_current_answerer(room_id)
        await room_cache.clear_answer_deadline(room_id)

        # 立即广播空抢答队列，避免前端残留上一轮队列状态
        await clients.broadcast(
            room_id,
            AnswerQueueMessage(data=AnswerQueueData(
                queue=[],
                answer_queue_tail_player_id=None,
            )).model_dump(),
        )

        # 2) 为新轮次生成可播放地址
        next_song_id = song_queue[next_index]
        audio_token = await crud.get_or_create_audio_token(session, room_id,
                                                           next_song_id)
        audio_url = get_audio_stream_url(audio_token)

        # 触发预下载（i = next_index）：下载 i+3 歌曲
        await preload_songs_for_round_start(
            clients=clients,
            session=session,
            room_id=room_id,
            song_queue=song_queue,
            current_index=next_index,
        )

        # 3) 更新播放状态并推进回合状态
        playback_state = cache_schemas.PlaybackState(
            play_state="playing",
            progress_ms=0,
            offset_ts=get_ts_ms(),
            audio_url=audio_url,
        )
        await room_cache.set_room_playback_state(room_id, playback_state)
        await handle_round_state_transition(clients, client, room_id,
                                            RoundState.PLAYING_AUDIO)

        # 4) 向所有客户端发送新一轮开始
        round_start_message = RoundStartMessage(data=RoundStartData(
            round_index=next_index,
            audio_url=audio_url,
            start_percent=0.0,
        ))
        await clients.broadcast(room_id, round_start_message.model_dump())

        # 兼容保留：广播原始SKIP_ROUND事件
        await clients.broadcast(room_id, data.model_dump())

        logger.info(
            "Skip round completed for room %s, moved from round %d to %d",
            room_id,
            current_index,
            next_index,
        )


@regist(GameEventType.ATTEMPT_ANSWER, AttemptAnswerMessage)
async def handle_attempt_answer(
    data: AttemptAnswerMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **kwargs: Any,
) -> None:
    """Handle ATTEMPT_ANSWER event: add player to answer queue."""
    # pylint: disable=too-many-locals,unused-argument
    if client is None or room_id is None:
        return

    # Validate round state: only allow attempts during PLAYING_AUDIO or ANSWERING
    async with session_scope() as check_db:
        round_state = await RoundStateManager.get_round_state(room_id, check_db)
    if round_state not in (RoundState.PLAYING_AUDIO, RoundState.ANSWERING):
        await client.send_error(
            GameEventType.ATTEMPT_ANSWER.value,
            "Cannot answer in the current round state",
        )
        return

    # 获取玩家ID和抢答时的时间/进度信息
    # 使用当前连接的认证用户ID，避免客户端伪造user_id
    player_id = client.user.id
    offset_ts = data.data.offset_ts

    # 生成服务器时间戳
    server_ts = get_ts_ms()

    # 添加到抢答队列 - 使用 room_cache.append_attempt_answer_player
    # 创建 AnswerQueueItem 对象
    answer_item = cache_schemas.AnswerQueueItem(
        player_id=player_id,
        offset_ts=offset_ts,
        server_ts=server_ts,
        order=None,
        is_answering=False,
    )
    try:
        result = await room_cache.append_attempt_answer_player(room_id, answer_item)
    except ValueError:
        logger.warning(
            "Player %s attempted to answer in room %s but is already in the queue",
            player_id,
            room_id,
        )
        await client.send_error(
            GameEventType.ATTEMPT_ANSWER.value,
            "You have already attempted to answer, please wait for next round",
        )
        return

    if result is None:
        logger.error("Failed to add player %s to answer queue in room %s", player_id,
                     room_id)
        await client.send_error(
            GameEventType.ATTEMPT_ANSWER.value,
            "Failed to join answer queue, please try again",
        )
        return

    logger.info(
        "Player %s attempted to answer in room %s at offset %d ms (server ts: %d)",
        player_id,
        room_id,
        offset_ts,
        server_ts,
    )

    playback_state = await room_cache.get_room_playback_state(room_id)
    if not playback_state:
        logger.warning(
            "No playback state found for room %s when player %s attempted to answer",
            room_id,
            player_id,
        )
        await client.send_error(
            GameEventType.ATTEMPT_ANSWER.value,
            "Failed to join answer queue: No playback state",
        )
        return

    # 当前无人作答：从队列头选出本轮作答者，开始作答阶段
    # NOTE: current_answerer 查询已移除——改用原子 CAS 操作代替
    # 原来的 get + set 是 TOCTOU 竞态：多个并发 ATTEMPT_ANSWER 都能看到 None，
    # 全部进入"首个作答者"路径，导致多次 PAUSE 广播和状态错误。
    answer_queue = await room_cache.get_answer_queue(room_id)
    if not answer_queue:
        logger.warning(
            "Answer queue is empty right after enqueue in room %s, player %s",
            room_id,
            player_id,
        )
        return

    queue_tail_player_id = await room_cache.get_answer_queue_tail_player_id(room_id)
    next_answerer = answer_queue[0].player_id
    if queue_tail_player_id is not None:
        queue_tail_index = next(
            (index for index, item in enumerate(answer_queue)
             if item.player_id == queue_tail_player_id),
            -1,
        )
        if queue_tail_index >= 0 and queue_tail_index + 1 < len(answer_queue):
            next_answerer = answer_queue[queue_tail_index + 1].player_id
        elif queue_tail_index < 0:
            next_answerer = player_id

    # 广播 ATTEMPT_ANSWER 给其他客户端，附带完整队列数据用于前端队列同步
    augmented_data = data.model_dump()
    augmented_data["data"]["queue"] = [item.model_dump() for item in answer_queue]
    augmented_data["data"]["answer_queue_tail_player_id"] = queue_tail_player_id
    await clients.broadcast(
        room_id,
        augmented_data,
        excluded_clients={client},
    )

    logger.debug(
        "Current playback state for room %s: %s",
        room_id,
        playback_state,
    )

    # 先准备好暂停状态（不产生副作用），再用原子 CAS 决定是否执行
    if playback_state.play_state == "playing":
        paused_progress_ms = playback_state.progress_ms
        if playback_state.offset_ts and playback_state.offset_ts > 0:
            paused_progress_ms = max(
                0,
                playback_state.progress_ms +
                max(0, server_ts - playback_state.offset_ts),
            )

        new_playback_state = playback_state.model_copy(
            update={
                "play_state": "paused",
                "progress_ms": paused_progress_ms,
                "offset_ts": server_ts,
            })
    else:
        new_playback_state = None

    # 原子 CAS：仅当当前**无人作答**时才设置成功
    set_ok = await room_cache.try_set_first_answerer(room_id, next_answerer)
    if not set_ok:
        logger.info(
            "Race lost for player %s in room %s: another answerer already set",
            player_id,
            room_id,
        )
        return

    # CAS 成功——现在安全执行副作用
    if new_playback_state is not None:
        await room_cache.set_room_playback_state(room_id, new_playback_state)
        await handle_round_state_transition(clients, client, room_id,
                                            RoundState.ANSWERING)

        pause_message = PauseMessage(data=PlayControlData(
            progress_ms=new_playback_state.progress_ms,
            offset_ts=server_ts,
            audio_url=new_playback_state.audio_url,
        ))
        await clients.broadcast(room_id, pause_message.model_dump())
    else:
        logger.info(
            "Playback already paused in room %s when first answerer %s is selected",
            room_id,
            next_answerer,
        )

    await room_cache.sync_answer_queue_is_answering(room_id, next_answerer)

    # 存储答题截止时间（30秒后）
    answer_deadline = server_ts + 30000
    await room_cache.set_answer_deadline(room_id, answer_deadline)

    # 广播YOUR_TURN事件给所有客户端（携带当前作答玩家ID和截止时间）
    your_turn_data = YourTurnData(
        user_id=int(next_answerer),
        answer_deadline=answer_deadline,
    )
    your_turn_message = YourTurnMessage(data=your_turn_data)
    await clients.broadcast(room_id, your_turn_message.model_dump())
    logger.info("Broadcast YOUR_TURN for player %s in room %s", next_answerer, room_id)


@regist(GameEventType.SUBMIT_ANSWER, SubmitAnswerMessage)
async def handle_submit_answer(
    data: SubmitAnswerMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **kwargs,
) -> None:
    """处理玩家提交答案事件"""
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements,unused-argument
    # Validate round state: allow submissions during PLAYING_AUDIO or ANSWERING
    async with session_scope() as check_db:
        round_state = await RoundStateManager.get_round_state(room_id, check_db)
    if round_state not in (RoundState.PLAYING_AUDIO, RoundState.ANSWERING):
        await client.send_error(
            GameEventType.SUBMIT_ANSWER,
            "Cannot submit answer in the current round state",
        )
        return

    # 验证当前玩家是否为当前作答者
    player_id = client.user.id
    player_id_str = str(player_id)
    current_answerer = await room_cache.get_room_current_answerer(room_id)
    if current_answerer is None or str(current_answerer) != player_id_str:
        await client.send_error(GameEventType.SUBMIT_ANSWER, "Not your turn to answer")
        return

    # 验证答题截止时间
    deadline = await room_cache.get_answer_deadline(room_id)
    if deadline is not None and get_ts_ms() > deadline:
        logger.warning(
            "Player %s submission expired in room %s (deadline=%d)",
            player_id_str,
            room_id,
            deadline,
        )
        await client.send_error(GameEventType.SUBMIT_ANSWER, "Answer time expired")

        # 答题超时：自动跳过当前玩家，移到队列中的下一人
        current_queue = await room_cache.get_answer_queue(room_id)
        if current_queue:
            current_index = -1
            for i, item in enumerate(current_queue):
                if str(item.player_id) == player_id_str:
                    current_index = i
                    break
            if current_index >= 0 and current_index + 1 < len(current_queue):
                next_player = current_queue[current_index + 1].player_id
                transition_ok = await room_cache.transition_room_current_answerer(
                    room_id, int(player_id), next_player)
                if transition_ok:
                    await room_cache.sync_answer_queue_is_answering(
                        room_id, next_player)
                    next_deadline = get_ts_ms() + 30000
                    await room_cache.set_answer_deadline(room_id, next_deadline)
                    your_turn_data = YourTurnData(
                        user_id=int(next_player),
                        answer_deadline=next_deadline,
                    )
                    await clients.broadcast(
                        room_id,
                        YourTurnMessage(data=your_turn_data).model_dump(),
                    )
            else:
                # 无后续玩家，恢复播放
                await room_cache.clear_answer_deadline(room_id)
                await room_cache.transition_room_current_answerer(
                    room_id, int(player_id), None)
                await room_cache.sync_answer_queue_is_answering(room_id, -1)
                await room_cache.set_answer_queue_tail_player_id(
                    room_id, int(player_id))
                playback_state = await room_cache.get_room_playback_state(room_id)
                if playback_state and playback_state.play_state == "paused":
                    server_ts = get_ts_ms()
                    new_ps = playback_state.model_copy(update={
                        "play_state": "playing",
                        "offset_ts": server_ts,
                    })
                    await room_cache.set_room_playback_state(room_id, new_ps)
                    await clients.broadcast(
                        room_id,
                        PlayMessage(data=PlayControlData(
                            progress_ms=new_ps.progress_ms,
                            offset_ts=server_ts,
                            audio_url=new_ps.audio_url,
                        )).model_dump(),
                    )
                current_queue = await room_cache.get_answer_queue(room_id)
                await clients.broadcast(
                    room_id,
                    AnswerQueueMessage(data=AnswerQueueData(
                        queue=current_queue,
                        answer_queue_tail_player_id=(
                            await room_cache.get_answer_queue_tail_player_id(room_id)),
                    )).model_dump(),
                )
        return

    # 将答案保存到数据库（PlayerAnswer表）
    try:
        async with session_scope() as db:
            # 获取当前歌曲信息
            song_id, song_index = await crud.get_current_song_info(db, room_id)
            if song_id is None or song_index is None:
                logger.warning(
                    "Cannot determine current song for room %s, skipping answer save",
                    room_id,
                )
                await client.send_error(GameEventType.SUBMIT_ANSWER,
                                        "Cannot determine current song")
                return

            # 创建玩家答案记录
            player_answer = models.PlayerAnswer(
                room_id=room_id,
                user_id=client.user.id,
                song_id=song_id,
                round_index=song_index,  # 使用歌曲索引作为轮次索引
                selected_tag_ids=data.data.selected_tag_ids,
                description_text=data.data.description_text,
                answer_order=None,  # 抢答顺序已在handle_judge_submit中设置
            )
            db.add(player_answer)
            logger.info(
                "Saved answer for player %s song %d in room %s",
                player_id_str,
                song_id,
                room_id,
            )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Failed to save player answer to database: %s", e)
        await client.send_error(GameEventType.SUBMIT_ANSWER, "Failed to save answer")
        return

    # 广播答案给所有玩家（ANSWER_BROADCAST事件）
    answer_broadcast_data = AnswerBroadcastData(
        player_id=player_id,
        selected_tag_ids=data.data.selected_tag_ids,
        description_text=data.data.description_text,
    )
    answer_broadcast_message = AnswerBroadcastMessage(data=answer_broadcast_data)
    await clients.broadcast(room_id, answer_broadcast_message.model_dump())

    # 玩家提交答案后不清除队列，只更新当前作答者状态
    # 注意：玩家保持在队列中，直到回合结束才清空队列
    # await room_manager.set_current_answerer(room_id, "")

    # 获取下一个玩家（如果队列中还有玩家）
    # 使用 room_cache.get_answer_queue 获取排序后的 AnswerQueueItem 列表
    current_queue = await room_cache.get_answer_queue(room_id)
    should_finish_answering = False

    if current_queue:
        # 找到当前玩家的位置
        current_player_index = -1
        for i, item in enumerate(current_queue):
            if str(item.player_id) == player_id_str:
                current_player_index = i
                break

        if current_player_index >= 0 and current_player_index + 1 < len(current_queue):
            # 有下一个玩家
            next_player_item = current_queue[current_player_index + 1]
            next_player = next_player_item.player_id
            # Use CAS to atomically check current == player_id before
            # transitioning.  Prevents TOCTOU where concurrent SUBMIT_ANSWER
            # handlers both read the same queue and overwrite each other.
            transition_ok = await room_cache.transition_room_current_answerer(
                room_id, int(player_id), next_player)
            if not transition_ok:
                logger.info(
                    "Answerer already transitioned away from player %s in room %s, "
                    "skipping duplicate SUBMIT_ANSWER",
                    player_id_str,
                    room_id,
                )
                return
            await room_cache.sync_answer_queue_is_answering(room_id, next_player)

            # 广播给全房间：无论目标连接是否存在，都要让前端状态一致
            # 目标玩家若已掉线，前端也应感知当前轮到谁，避免停在旧状态
            next_client_found = any(room_client.user.id == next_player
                                    for room_client in clients.get_clients(room_id))
            if not next_client_found:
                logger.warning(
                    "Next player %s has no active WebSocket in room %s, "
                    "still broadcasting YOUR_TURN",
                    next_player,
                    room_id,
                )

            # 存储下一个玩家的答题截止时间
            next_deadline = get_ts_ms() + 30000
            await room_cache.set_answer_deadline(room_id, next_deadline)

            your_turn_data = YourTurnData(
                user_id=int(next_player),
                answer_deadline=next_deadline,
            )
            your_turn_message = YourTurnMessage(data=your_turn_data)
            await clients.broadcast(room_id, your_turn_message.model_dump())
            logger.info(
                "Broadcast YOUR_TURN to room %s for next player %s",
                room_id,
                next_player,
            )
            logger.info("Next player in queue: %s", next_player)
        else:
            # 队列无后续玩家（包括当前玩家未命中队列），恢复播放
            await room_cache.clear_answer_deadline(room_id)
            transition_ok = await room_cache.transition_room_current_answerer(
                room_id, int(player_id), None)
            if not transition_ok:
                logger.info(
                    "Answerer already transitioned away from player %s in room %s, "
                    "skipping duplicate SUBMIT_ANSWER",
                    player_id_str,
                    room_id,
                )
                return
            await room_cache.sync_answer_queue_is_answering(room_id, -1)
            await room_cache.set_answer_queue_tail_player_id(room_id, int(player_id))
            should_finish_answering = True
            logger.info(
                "No next player in queue after player %s submission in room %s",
                player_id_str,
                room_id,
            )
    else:
        # 队列为空，作答阶段结束，默认不自动恢复播放
        should_finish_answering = True

    if should_finish_answering:
        logger.info(
            "Answering phase finished in room %s, auto-resuming playback",
            room_id,
        )
        # 所有已抢答玩家完成作答后，自动恢复曲目播放。
        # 房主可随时手动发送 JUDGING 事件触发判分。
        playback_state = await room_cache.get_room_playback_state(room_id)
        if playback_state and playback_state.play_state == "paused":
            server_ts = get_ts_ms()
            new_playback_state = playback_state.model_copy(update={
                "play_state": "playing",
                "offset_ts": server_ts,
            })
            await room_cache.set_room_playback_state(room_id, new_playback_state)
            play_message = PlayMessage(data=PlayControlData(
                progress_ms=new_playback_state.progress_ms,
                offset_ts=server_ts,
                audio_url=new_playback_state.audio_url,
            ))
            await clients.broadcast(room_id, play_message.model_dump())

    # 广播更新后的抢答队列（使用与handle_attempt_answer相同的格式）
    # 注意：这里重新读取，确保包含最新 is_answering 状态
    current_queue = await room_cache.get_answer_queue(room_id)
    queue_tail_player_id = await room_cache.get_answer_queue_tail_player_id(room_id)
    answer_queue_data = AnswerQueueData(
        queue=current_queue,
        answer_queue_tail_player_id=queue_tail_player_id,
    )
    answer_queue_message = AnswerQueueMessage(data=answer_queue_data)
    await clients.broadcast(room_id, answer_queue_message.model_dump())

    logger.info("Answer submitted by player %s in room %s", player_id, room_id)
