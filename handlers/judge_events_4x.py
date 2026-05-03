"""WebSocket judge event handlers."""

from __future__ import annotations

import random

from sqlalchemy import select, func

from cache import room_cache
from cache.room_state_manager import RoundStateManager, RoomStateManager
from client_manager import ClientManager, Client
from db import models
from db.crud import (
    fetch_room_object,
    get_current_song_info,
    get_player_answers_for_judging,
    get_tag_group_map,
    save_score_record,
    update_player_answer_order,
    get_room_song_queue,
)
from db.session import session_scope
from handlers.registe_manager import regist
from handlers.round_state_events import build_round_state_update_message
from schemas.ws_messages.judge_schemas import (
    DescriptionCandidate,
    JudgeSubmitMessage,
    JudgingData,
    JudgingMessage,
    PlayerAnswerData,
    PlayerDescriptionData,
    ScoreEntry,
    ScoreUpdateData,
    ScoreUpdateMessage,
    ShowAnswerData,
    ShowAnswerMessage,
    ShowSongData,
    ShowSongMessage,
    ShowSongRequestMessage,
    SongInfo,
    TagData,
    TagGroupData,
)
from schemas.ws_messages.round_event_schemas import (
    RoundEndMessage,)
from schemas.ws_messages import room_schemas as RoomSchema
from utils import get_logger
from utils.calculate import calculate_player_scores
from utils.enumerations import GameEventType, RoundState

logger = get_logger(__name__)


@regist(GameEventType.SHOW_SONG, ShowSongRequestMessage)
async def handle_show_song(
    data: ShowSongRequestMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **kwargs,
) -> None:
    """Handle SHOW_SONG event: broadcast current song metadata to all clients."""
    # pylint: disable=unused-argument,too-many-locals
    if not client.user.is_owner:
        await client.send_error(GameEventType.SHOW_SONG,
                                "Only owner can show current song")
        return

    async with session_scope() as session:
        song_id, _ = await get_current_song_info(session, room_id)
        if song_id is None:
            await client.send_error(GameEventType.SHOW_SONG,
                                    "Cannot determine current song")
            return

        song_stmt = select(models.Song).where(models.Song.id == song_id)
        song_result = await session.execute(song_stmt)
        song = song_result.scalar_one_or_none()
        if not song:
            await client.send_error(GameEventType.SHOW_SONG, "Song not found")
            return

        room_stmt = select(models.Room).where(models.Room.id == room_id)
        room_result = await session.execute(room_stmt)
        room = room_result.scalar_one_or_none()
        if room is not None:
            room.show_answer = True

        show_song_message = ShowSongMessage(data=ShowSongData(
            title=song.title,
            album=song.album_name,
            author=song.artist,
            cover=song.cover_url,
        ))
        await clients.broadcast(room_id, show_song_message.model_dump())

        # 同步 show_answer=True 到 Redis PlaybackState，确保所有客户端通过播放事件也能获取最新状态
        existing_playback = await room_cache.get_room_playback_state(room_id)
        if existing_playback is not None and not existing_playback.show_answer:
            updated = existing_playback.model_copy(update={"show_answer": True})
            await room_cache.set_room_playback_state(room_id, updated)

        logger.info("Broadcast SHOW_SONG for room %s, song_id=%s", room_id, song_id)


async def broadcast_judging_event(  # pylint: disable=too-many-locals
    clients: ClientManager,
    room_id: str,
    db_session,
) -> None:
    """广播 JUDGING 事件给所有客户端

    这个函数可以在多种情况下被调用：
    1. 房主手动触发
    2. 曲目播放完成
    3. 所有玩家回答完成

    Args:
        clients: 客户端管理器
        room_id: 房间 ID
        db_session: 数据库会话
    """
    # 获取房间对象（包含标签组信息）
    room = await fetch_room_object(db_session, room_id)
    if not room:
        logger.error("Room %s not found for judging", room_id)
        return

    # 获取当前歌曲信息
    song_id, song_index = await get_current_song_info(db_session, room_id)
    if song_id is None or song_index is None:
        logger.error("Cannot determine current song for room %s", room_id)
        return

    # 获取当前歌曲
    song_stmt = select(models.Song).where(models.Song.id == song_id)
    song_result = await db_session.execute(song_stmt)
    song = song_result.scalar_one_or_none()
    if not song:
        logger.error("Song %s not found for room %s", song_id, room_id)
        return

    # 构建标签组数据
    tag_groups_data = []
    for tag_group in room.tag_groups:
        tags = [TagData(id=tag.id, name=tag.name) for tag in tag_group.tags]
        tag_groups_data.append(
            TagGroupData(
                group_id=tag_group.id,
                name=tag_group.name,
                tags=tags,
            ))

    # 获取历史正确描述（用于展示候选）
    # 按 times_selected 降序排序，最多取 10 条
    description_history_stmt = select(models.SongDescriptionHistory).where(
        models.SongDescriptionHistory.song_id == song_id,
        models.SongDescriptionHistory.is_correct,
    ).order_by(models.SongDescriptionHistory.times_selected.desc()).limit(10)
    description_history_result = await db_session.execute(description_history_stmt)
    description_history_records = description_history_result.scalars().all()

    # 随机选择 1-3 条参考描述
    description_candidates = []
    if description_history_records:
        # 随机打乱并取 1-3 条
        shuffled = list(description_history_records)
        random.shuffle(shuffled)
        selected = shuffled[:min(random.randint(1, 3), len(shuffled))]
        description_candidates = [
            DescriptionCandidate(
                id=record.id,
                text=record.description_text,
                count=record.times_selected,
            ) for record in selected
        ]

    # 获取玩家答案（用于显示）
    player_answers = await get_player_answers_for_judging(db_session, room_id, song_id,
                                                          song_index)

    # 获取历史标签（按出现次数排序）
    history_tags_stmt = (select(
        models.SongTagHistory.tag_id,
        func.count(models.SongTagHistory.id).label("selected_count"),
    ).where(models.SongTagHistory.song_id == song_id).group_by(
        models.SongTagHistory.tag_id).order_by(
            func.count(models.SongTagHistory.id).desc(),
            models.SongTagHistory.tag_id.asc(),
        ))
    history_tags_result = await db_session.execute(history_tags_stmt)
    history_tag_ids = [row.tag_id for row in history_tags_result]

    logger.info("Player answers for judging: %s", player_answers)

    # Batch fetch all usernames to avoid N+1 queries
    user_ids = list(player_answers.keys())
    usernames_stmt = select(models.User.id,
                            models.User.username).where(models.User.id.in_(user_ids))
    usernames_result = await db_session.execute(usernames_stmt)
    username_map = {row.id: row.username for row in usernames_result}

    # 构建玩家答案列表
    player_answers_data = []
    player_descriptions_data = []  # 用于房主选择正确答案的描述列表

    for user_id, answer_data in player_answers.items():
        username = username_map.get(user_id, f"Player {user_id}")

        player_answers_data.append(
            PlayerAnswerData(
                player_id=user_id,
                username=username,
                selected_tags=answer_data["selected_tag_ids"],
                description=answer_data["description_text"],
            ))

        # 如果玩家有提交描述，加入 player_descriptions
        if answer_data.get("description_text"):
            logger.info("Adding player description from user %s: %s", username,
                        answer_data["description_text"])
            player_descriptions_data.append(
                PlayerDescriptionData(
                    id=user_id,  # 使用玩家 ID 作为描述的唯一标识
                    username=username,
                    description=answer_data["description_text"],
                ))
        else:
            logger.info("Player %s has no description text", username)

    logger.info("Built %d player descriptions", len(player_descriptions_data))

    # 构建 JUDGING 事件数据
    judging_data = JudgingData(
        song=SongInfo(
            id=song.id,
            title=song.title,
            artist=song.artist,
            album=song.album_name,
            cover_url=song.cover_url,
            platform_url=None,
        ),
        history_tag_ids=history_tag_ids,
        reference_descriptions=[candidate.text for candidate in description_candidates],
        tag_groups=tag_groups_data,
        description_candidates=description_candidates,
        answers=player_answers_data,
        player_descriptions=player_descriptions_data,
    )

    # 触发状态转换到 JUDGING
    success = await RoundStateManager.transition_round_state(
        room_id=room_id,
        target=RoundState.JUDGING,
        session=db_session,
    )
    if success:
        await clients.broadcast(
            room_id,
            build_round_state_update_message(RoundState.JUDGING).model_dump(),
        )
        logger.info("Room %s round state transitioned to JUDGING", room_id)

    # 广播 JUDGING 事件给所有客户端
    judging_message = JudgingMessage(data=judging_data)
    message_dict = judging_message.model_dump()

    logger.info(
        "Sending JUDGING event with %d player answers and %d player descriptions",
        len(player_answers_data), len(player_descriptions_data))
    logger.info("Player descriptions data: %s", player_descriptions_data)

    await clients.broadcast(room_id, message_dict)

    logger.info("Sent JUDGING event for room %s, song %s", room_id, song.title)


@regist(GameEventType.JUDGING, JudgingMessage)
async def handle_judging(data: JudgingMessage, clients: ClientManager, client: Client,
                         room_id: str, **kwargs) -> None:
    """处理进入判分环节事件（房主手动触发）"""
    if not client.user.is_owner:
        await client.send_error(GameEventType.JUDGING, "Only owner can trigger judging")
        return
    # pylint: disable=unused-argument
    try:
        async with session_scope() as db:
            await broadcast_judging_event(clients, room_id, db)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error during JUDGING event: %s", e)
        if client:
            await client.send_error(GameEventType.JUDGING, "Internal server error")


@regist(GameEventType.JUDGE_SUBMIT, JudgeSubmitMessage)
async def handle_judge_submit(  # pylint: disable=too-many-return-statements
    data: JudgeSubmitMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **kwargs,
) -> None:
    """处理房主提交正确答案事件"""
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements,unused-argument
    # 验证房主身份
    if not client.user.is_owner:
        await client.send_error(GameEventType.JUDGE_SUBMIT,
                                "Only owner can submit judge result")
        return

    # 如果选择跳过计分，直接结束
    if data.data.skip_scoring:
        logger.info("Skipping scoring for room %s", room_id)
        try:
            async with session_scope() as session:
                success = await RoundStateManager.transition_round_state(
                    room_id=room_id,
                    target=RoundState.COMPLETED,
                    session=session,
                )
                if success:
                    await clients.broadcast(
                        room_id,
                        build_round_state_update_message(
                            RoundState.COMPLETED).model_dump(),
                    )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Failed to transition to COMPLETED when skip scoring: %s", e)
        # 广播ROUND_END事件
        round_end_message = RoundEndMessage(event=GameEventType.ROUND_END.value)
        await clients.broadcast(room_id, round_end_message.model_dump())
        return

    # 初始化需要用到的变量（确保在所有作用域内可用）
    players: list[dict[str, int | str]] = []
    player_scores: dict[int, int] = {}

    # Variables collected during DB session for post-commit broadcasts
    show_answer_data: ShowAnswerData | None = None
    score_entries: list[ScoreEntry] = []
    transition_success: bool = False
    game_end_result: dict | None = None

    # Single session_scope: ALL DB work (scores, history, round state, game end check)
    try:
        async with session_scope() as db:
            # 检查房间是否存在
            room = await fetch_room_object(db, room_id)
            if not room:
                await client.send_error(GameEventType.JUDGE_SUBMIT, "Room not found")
                return

            # 获取当前歌曲信息
            song_id, song_index = await get_current_song_info(db, room_id)
            if song_id is None or song_index is None:
                await client.send_error(GameEventType.JUDGE_SUBMIT,
                                        "Cannot determine current song")
                return

            # 存储标签历史（每次判分都记录，用于统计历史选择次数）
            for tag_id in data.data.correct_tags:
                tag_history = models.SongTagHistory(
                    song_id=song_id,
                    tag_id=tag_id,
                    judged_by_user_id=client.user.id,
                    room_id=room_id,
                )
                db.add(tag_history)

            # 处理玩家选择的描述（增加 times_selected）
            for description_id in data.data.correct_description_ids:
                existing_desc_stmt = select(models.SongDescriptionHistory).where(
                    models.SongDescriptionHistory.id == description_id,
                    models.SongDescriptionHistory.song_id == song_id,
                )
                existing_desc_result = await db.execute(existing_desc_stmt)
                existing_desc = existing_desc_result.scalar_one_or_none()

                if existing_desc:
                    existing_desc.times_selected += 1
                else:
                    answer_stmt = select(models.PlayerAnswer).where(
                        models.PlayerAnswer.room_id == room_id,
                        models.PlayerAnswer.song_id == song_id,
                        models.PlayerAnswer.round_index == song_index,
                        models.PlayerAnswer.user_id == description_id,
                    )
                    answer_result = await db.execute(answer_stmt)
                    answer = answer_result.scalar_one_or_none()

                    if answer and answer.description_text:
                        description_history = models.SongDescriptionHistory(
                            song_id=song_id,
                            description_text=answer.description_text,
                            is_correct=True,
                            times_selected=1,
                            judged_by_user_id=client.user.id,
                            room_id=room_id,
                        )
                        db.add(description_history)

            # 处理新的正确描述
            for description_text in data.data.new_correct_descriptions:
                if description_text.strip():
                    description_history = models.SongDescriptionHistory(
                        song_id=song_id,
                        description_text=description_text.strip(),
                        is_correct=True,
                        times_selected=1,
                        judged_by_user_id=client.user.id,
                        room_id=room_id,
                    )
                    db.add(description_history)

            # 获取房间玩家
            players = [{
                "id": user.id,
                "username": user.username
            } for user in room.users]

            # 获取抢答队列
            # pylint: disable=no-value-for-parameter  # decorator adds redis internally
            answer_queue_items = await room_cache.get_answer_queue(room_id)
            answer_queue = [item.player_id for item in answer_queue_items]

            if not answer_queue:
                logger.warning("Empty answer queue for judging in room %s", room_id)

            # 从数据库获取玩家答案
            player_answers_raw = await get_player_answers_for_judging(
                db, room_id, song_id, song_index)

            if not player_answers_raw:
                logger.warning(
                    "No player answers found for judging in room %s, song %s",
                    room_id,
                    song_id,
                )

            # 转换格式
            player_answers = {}
            for user_id, answer_data in player_answers_raw.items():
                player_answers[user_id] = {
                    "selected_tag_ids": answer_data["selected_tag_ids"],
                    "description_text": answer_data["description_text"],
                    "answer_order": answer_data["answer_order"],
                }

            # 获取标签组映射
            tag_group_map = await get_tag_group_map(db, room_id)

            # 计算玩家得分
            player_scores = calculate_player_scores(
                answer_queue=answer_queue,
                player_answers=player_answers,
                tag_group_map=tag_group_map,
                correct_tags=data.data.correct_tags.copy(),
                correct_description_ids=data.data.correct_description_ids,
            )

            # 确保所有房间玩家都有得分记录（即使为0）
            for player in players:
                player_id = int(player["id"])
                if player_id not in player_scores:
                    player_scores[player_id] = 0

            # 更新数据库中的抢答顺序
            updated_count = await update_player_answer_order(db, room_id, song_id,
                                                             song_index, answer_queue)
            logger.info("Updated answer_order for %d players in room %s", updated_count,
                        room_id)

            # 保存得分记录到数据库
            for player_id, score_delta in player_scores.items():
                if score_delta > 0:
                    try:
                        await save_score_record(db, room_id, player_id, song_index,
                                                score_delta)
                    except (ValueError, Exception) as e:  # pylint: disable=broad-exception-caught
                        logger.error("Failed to save score for player %s: %s",
                                     player_id, e)

            logger.info("Saved scoring results to database for room %s", room_id)

            # Collect broadcast data for SHOW_ANSWER
            show_answer_data = ShowAnswerData(
                tag_ids=data.data.correct_tags,
                description_ids=data.data.correct_description_ids,
            )

            # Collect broadcast data for SCORE_UPDATE
            score_entries = [
                ScoreEntry(
                    player_id=player_id,
                    username=next(
                        (str(p["username"]) for p in players if p["id"] == player_id),
                        f"Player {player_id}",
                    ),
                    score=player_scores.get(player_id, 0),
                ) for player_id in player_scores
            ]

            # 判分完成后，回合状态转移到 COMPLETED
            transition_success = await RoundStateManager.transition_round_state(
                room_id=room_id,
                target=RoundState.COMPLETED,
                session=db,
            )
            if transition_success:
                logger.info("Room %s round state transitioned to COMPLETED", room_id)

            # 检查是否所有歌曲都已播放完毕，如果是则自动结束游戏
            song_queue = await get_room_song_queue(db, room_id)
            if song_queue:
                current_index = room.current_song_index or 0
                if current_index >= len(song_queue) - 1:
                    logger.info(
                        "All songs completed for room %s (current=%d, total=%d), "
                        "auto-ending game",
                        room_id,
                        current_index,
                        len(song_queue),
                    )
                    game_end_result = await RoomStateManager.end_game(room_id, db)
            else:
                logger.warning("Room %s has no songs when checking game end", room_id)

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error during judge submission for room %s: %s", room_id, e)
        await client.send_error(GameEventType.JUDGE_SUBMIT.value,
                                "Internal server error")
        return

    # 计分完成后保留当前回合抢答信息，直到下一轮开始时再统一重置。
    # 这样前端在 COMPLETED 阶段仍可展示本轮抢答/作答上下文。

    # --- Post-commit: all Redis operations and WebSocket broadcasts ---

    # 广播正确答案（SHOW_ANSWER事件）
    show_answer_message = ShowAnswerMessage(data=show_answer_data)
    await clients.broadcast(room_id, show_answer_message.model_dump())

    # 广播得分更新事件
    score_update_data = ScoreUpdateData(scores=score_entries)
    score_update_message = ScoreUpdateMessage(data=score_update_data)
    await clients.broadcast(room_id, score_update_message.model_dump())

    logger.info("Scoring completed for room %s", room_id)

    # 广播回合状态更新
    if transition_success:
        await clients.broadcast(
            room_id,
            build_round_state_update_message(RoundState.COMPLETED).model_dump(),
        )

    # 广播游戏结束（如果自动结束）
    if game_end_result is not None:
        if game_end_result["success"]:
            game_over_data = RoomSchema.GameOverData(
                manual=False, final_scores=game_end_result["final_scores"])
            message = RoomSchema.GameOverMessage(data=game_over_data)
            await clients.broadcast(room_id, message.model_dump())
            logger.info("Game auto-ended for room %s", room_id)
        else:
            logger.error("Failed to auto-end game for room %s: %s", room_id,
                         game_end_result.get("error"))
