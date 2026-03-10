"""WebSocket round event handlers."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

import cache.schemas as cache_schemas
from cache import room_cache
from cache.room_state_manager import RoomStateManager
from client_manager import ClientManager, Client
from db import crud
from db import models
from db.session import session_scope
from handlers.audio_common import trigger_and_broadcast_preload_for_index
from handlers.registe_manager import regist
from handlers.round_state_events import handle_round_state_transition
from schemas.ws_messages.judge_schemas import SkipRoundMessage
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
from utils import get_audio_stream_url, get_logger
from utils.enumerations import ErrorEventType, GameEventType, RoundState
from utils.ts import get_ts_ms

logger = get_logger(__name__)


@regist(GameEventType.GAME_START, data_validator=GameStartMessage)
async def handle_game_start(
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

        # 使用 RoomStateManager 处理游戏开始
        if not await RoomStateManager.start_game(room_id, session):
            await client.send_error(GameEventType.GAME_START, "Failed to start game")
            return

        # 广播游戏开始消息
        await clients.broadcast(room_id, data.model_dump())

        # 生成临时音频访问令牌（与后续轮次统一走CRUD，确保DB/Redis一致）
        first_song_id = song_queue[0]
        audio_token = await crud.get_or_create_audio_token(session, room_id,
                                                           first_song_id)
        audio_url = get_audio_stream_url(audio_token)

        # 触发第 i+3 首预加载并广播 PRELOAD_AUDIO（i=0）
        await trigger_and_broadcast_preload_for_index(
            clients=clients,
            session=session,
            room_id=room_id,
            song_queue=song_queue,
            preload_index=3,
        )

        # 立即发送第一个回合开始事件
        await crud.update_room_current_song_index(session, room_id, 0)
        playback_state = cache_schemas.PlaybackState(
            play_state="playing",
            progress_ms=0,
            offset_ts=0,
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
                                                      start_pertent=0.0)).model_dump(),
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

        # 清理上一轮的作答状态
        await room_cache.clear_answer_queue(room_id)
        await room_cache.set_room_current_answerer(room_id, "")

        # 2) 为新轮次生成可播放地址
        next_song_id = song_queue[next_index]
        audio_token = await crud.get_or_create_audio_token(session, room_id,
                                                           next_song_id)
        audio_url = get_audio_stream_url(audio_token)

        # 触发第 i+3 首预加载并广播 PRELOAD_AUDIO
        await trigger_and_broadcast_preload_for_index(
            clients=clients,
            session=session,
            room_id=room_id,
            song_queue=song_queue,
            preload_index=next_index + 3,
        )

        # 3) 更新播放状态并推进回合状态
        playback_state = cache_schemas.PlaybackState(
            play_state="playing",
            progress_ms=0,
            offset_ts=0,
            audio_url=audio_url,
        )
        await room_cache.set_room_playback_state(room_id, playback_state)
        await handle_round_state_transition(clients, client, room_id,
                                            RoundState.PLAYING_AUDIO)

        # 4) 向所有客户端发送新一轮开始
        round_start_message = RoundStartMessage(data=RoundStartData(
            round_index=next_index,
            audio_url=audio_url,
            start_pertent=0.0,
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

    # 获取玩家ID和offset_ts
    player_id = str(data.data.user_id)
    offset_ts = data.data.offset_ts

    # 生成服务器时间戳
    server_ts = get_ts_ms()

    # 获取当前队列（检查是否为空）
    current_queue = await room_cache.get_answer_queue(room_id)
    was_empty = len(current_queue) == 0

    # 添加到抢答队列 - 使用 room_cache.append_attempt_answer_player
    # 创建 AnswerQueueItem 对象
    answer_item = cache_schemas.AnswerQueueItem(
        player_id=int(player_id),
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

    # 如果此前队列为空，暂停播放并设置当前作答玩家
    if was_empty:
        # 获取当前播放进度（从Redis房间状态）
        current_progress = await room_cache.get_room_play_progress(room_id)

        # 更新Redis播放状态
        await room_cache.update_room_playback_state(
            room_id=room_id,
            round_state=RoundState.ANSWERING.name,
            progress_ms=current_progress,
            offset_ts=offset_ts,  # 使用抢答时间戳作为offset_ts
            audio_url=None,
            event_ts=server_ts,
            event_name="PAUSE",
        )

        # 触发状态转换到 ANSWERING（并广播）
        await handle_round_state_transition(clients, client, room_id,
                                            RoundState.ANSWERING)

        # 设置当前作答玩家
        await room_cache.set_room_current_answerer(room_id, player_id)

        # 发送YOUR_TURN事件给当前作答玩家
        your_turn_data = YourTurnData()
        your_turn_message = YourTurnMessage(data=your_turn_data)
        await client.send(your_turn_message.model_dump())
        logger.info("Sent YOUR_TURN to player %s in room %s", player_id, room_id)

    # 获取更新后的排序队列 - 使用 room_cache.get_answer_queue
    sorted_queue = [*(await room_cache.get_answer_queue(room_id))]

    # 广播ATTEMPT_ANSWER事件给其他客户端（保持原有行为）
    await clients.broadcast(
        room_id,
        data.model_dump(),
        excluded_clients={client},
    )

    answer_queue_data = AnswerQueueData(queue=sorted_queue)
    answer_queue_message = AnswerQueueMessage(data=answer_queue_data)

    await clients.broadcast(
        room_id,
        answer_queue_message.model_dump(),
    )

    logger.info("Answer queue updated in room %s: %s", room_id, sorted_queue)


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
    # 验证当前玩家是否为当前作答者
    player_id = str(client.user.id)
    current_answerer = await room_cache.get_room_current_answerer(room_id)
    if current_answerer != player_id:
        await client.send_error(GameEventType.SUBMIT_ANSWER, "Not your turn to answer")
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
                player_id,
                song_id,
                room_id,
            )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Failed to save player answer to database: %s", e)
        await client.send_error(GameEventType.SUBMIT_ANSWER,
                                f"Failed to save answer: {str(e)}")
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
    if current_queue:
        # 找到当前玩家的位置
        current_player_index = -1
        for i, item in enumerate(current_queue):
            if str(item.player_id) == player_id:
                current_player_index = i
                break

        if current_player_index >= 0 and current_player_index + 1 < len(current_queue):
            # 有下一个玩家
            next_player_item = current_queue[current_player_index + 1]
            next_player = str(next_player_item.player_id)
            await room_cache.set_room_current_answerer(room_id, next_player)

            # 在房间客户端中查找下一个玩家的连接
            next_client_found = None
            for room_client in clients.get_clients(room_id):
                if str(room_client.user.id) == next_player:
                    next_client_found = room_client
                    break

            if next_client_found:
                your_turn_data = YourTurnData()
                your_turn_message = YourTurnMessage(data=your_turn_data)
                await next_client_found.send(your_turn_message.model_dump())
                logger.info("Sent YOUR_TURN to next player %s in room %s", next_player,
                            room_id)
            else:
                logger.warning(
                    "Could not find WebSocket for next player %s in room %s",
                    next_player,
                    room_id,
                )
            logger.info("Next player in queue: %s", next_player)
        else:
            # 没有下一个玩家，清空当前作答者
            await room_cache.set_room_current_answerer(room_id, "")
            logger.info(
                "No next player in queue after player %s submission in room %s",
                player_id,
                room_id,
            )
    else:
        # 队列为空，恢复播放？
        # 暂时不处理，由房主控制
        logger.info("Answer queue empty after submission in room %s", room_id)

    # 广播更新后的抢答队列（使用与handle_attempt_answer相同的格式）
    answer_queue_data = AnswerQueueData(queue=current_queue)
    answer_queue_message = AnswerQueueMessage(data=answer_queue_data)
    await clients.broadcast(room_id, answer_queue_message.model_dump())

    logger.info("Answer submitted by player %s in room %s", player_id, room_id)
