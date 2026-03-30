"""WebSocket judge event handlers."""

from __future__ import annotations

import random

from sqlalchemy import select

from cache import room_cache
from cache.room_state_manager import RoundStateManager
from client_manager import ClientManager, Client
from db import models
from db.crud import (
    fetch_room_object,
    get_current_song_info,
    get_player_answers_for_judging,
    get_tag_group_map,
    save_score_record,
    update_player_answer_order,
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
    TagData,
    TagGroupData,
)
from schemas.ws_messages.round_event_schemas import (
    RoundEndMessage,)
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
    # pylint: disable=unused-argument
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

        show_song_message = ShowSongMessage(data=ShowSongData(
            title=song.title,
            album=song.album_name,
            author=song.artist,
            cover=song.cover_url,
        ))
        await clients.broadcast(room_id, show_song_message.model_dump())

        logger.info("Broadcast SHOW_SONG for room %s, song_id=%s", room_id, song_id)


@regist(GameEventType.JUDGING, JudgingMessage)
async def handle_judging(data: JudgingMessage, clients: ClientManager, client: Client,
                         room_id: str, **kwargs) -> None:
    """处理进入判分环节事件"""
    # pylint: disable=unused-argument,too-many-locals
    try:
        async with session_scope() as db:
            # 获取房间对象（包含标签组信息）
            room = await fetch_room_object(db, room_id)
            if not room:
                await client.send_error(GameEventType.JUDGING, "Room not found")
                return

            # 获取当前歌曲信息
            song_id, song_index = await get_current_song_info(db, room_id)
            if song_id is None or song_index is None:
                await client.send_error(GameEventType.JUDGING,
                                        "Cannot determine current song")
                return

            # 获取当前歌曲
            song_stmt = select(models.Song).where(models.Song.id == song_id)
            song_result = await db.execute(song_stmt)
            song = song_result.scalar_one_or_none()
            if not song:
                await client.send_error(GameEventType.JUDGING, "Song not found")
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
            # 按 times_selected 降序排序，最多取10条
            description_history_stmt = select(models.SongDescriptionHistory).where(
                models.SongDescriptionHistory.song_id == song_id,
                models.SongDescriptionHistory.is_correct == True,  # pylint: disable=singleton-comparison
            ).order_by(models.SongDescriptionHistory.times_selected.desc()).limit(10)
            description_history_result = await db.execute(description_history_stmt)
            description_history_records = description_history_result.scalars().all()

            # 随机选择1-3条参考描述
            description_candidates = []
            if description_history_records:
                # 随机打乱并取1-3条
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
            player_answers = await get_player_answers_for_judging(
                db, room_id, song_id, song_index)

            # 构建玩家答案列表
            player_answers_data = []
            player_descriptions_data = []  # 用于房主选择正确答案的描述列表
            
            for user_id, answer_data in player_answers.items():
                # 获取用户名
                user_stmt = select(
                    models.User.username).where(models.User.id == user_id)
                user_result = await db.execute(user_stmt)
                username = user_result.scalar_one_or_none() or f"Player {user_id}"

                player_answers_data.append(
                    PlayerAnswerData(
                        player_id=user_id,
                        username=username,
                        selected_tags=answer_data["selected_tag_ids"],
                        description=answer_data["description_text"],
                    ))
                
                # 如果玩家有提交描述，加入 player_descriptions
                if answer_data.get("description_text"):
                    player_descriptions_data.append(
                        PlayerDescriptionData(
                            id=user_id,  # 使用玩家 ID 作为描述的唯一标识
                            username=username,
                            description=answer_data["description_text"],
                        ))

            # 构建 JUDGING 事件数据
            judging_data = JudgingData(
                tag_groups=tag_groups_data,
                description_candidates=description_candidates,
                answers=player_answers_data,
                player_descriptions=player_descriptions_data,
            )

            # 触发状态转换到 JUDGING
            success = await RoundStateManager.transition_round_state(
                room_id=room_id,
                target=RoundState.JUDGING,
                session=db,
            )
            if success:
                await clients.broadcast(
                    room_id,
                    build_round_state_update_message(RoundState.JUDGING).model_dump(),
                )
                logger.info("Room %s round state transitioned to JUDGING", room_id)

            # 广播JUDGING事件给所有客户端
            judging_message = JudgingMessage(data=judging_data)
            await clients.broadcast(room_id, judging_message.model_dump())

            logger.info("Sent JUDGING event for room %s, song %s", room_id, song.title)

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error during JUDGING event: %s", e)
        await client.send_error(GameEventType.JUDGING,
                                f"Internal server error: {str(e)}")


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
        round_end_message = RoundEndMessage()
        await clients.broadcast(room_id, round_end_message.model_dump())
        return

    # 初始化需要用到的变量（确保在所有作用域内可用）
    players: list[dict[str, str]] = []
    player_scores: dict[str, int] = {}

    # 使用数据库会话获取数据
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

            # 存储标签历史
            for tag_id in data.data.correct_tags:
                existing_stmt = select(models.SongTagHistory).where(
                    models.SongTagHistory.song_id == song_id,
                    models.SongTagHistory.tag_id == tag_id,
                )
                existing_result = await db.execute(existing_stmt)
                existing = existing_result.scalar_one_or_none()

                if not existing:
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
                "id": str(user.id),
                "username": user.username
            } for user in room.users]

            # 获取抢答队列
            # pylint: disable=no-value-for-parameter  # decorator adds redis internally
            answer_queue_items = await room_cache.get_answer_queue(room_id)
            answer_queue = [str(item.player_id) for item in answer_queue_items]

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
            for user_id_int, answer_data in player_answers_raw.items():
                player_id_str = str(user_id_int)
                player_answers[player_id_str] = {
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
                if player["id"] not in player_scores:
                    player_scores[player["id"]] = 0

            # 更新数据库中的抢答顺序
            updated_count = await update_player_answer_order(db, room_id, song_id,
                                                             song_index, answer_queue)
            logger.info("Updated answer_order for %d players in room %s", updated_count,
                        room_id)

            # 保存得分记录到数据库
            for player_id_str, score_delta in player_scores.items():
                if score_delta > 0:
                    try:
                        user_id = int(player_id_str)
                        await save_score_record(db, room_id, user_id, song_index,
                                                score_delta)
                    except (ValueError, Exception) as e:  # pylint: disable=broad-exception-caught
                        logger.error("Failed to save score for player %s: %s",
                                     player_id_str, e)

            logger.info("Saved scoring results to database for room %s", room_id)

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error during judge submission for room %s: %s", room_id, e)
        await client.send_error(GameEventType.JUDGE_SUBMIT.value,
                                f"Internal server error: {str(e)}")
        return

    # 计分完成，清空抢答队列
    # pylint: disable=no-value-for-parameter  # decorator adds redis internally
    await room_cache.clear_answer_queue(room_id)

    # 广播正确答案（SHOW_ANSWER事件）
    show_answer_data = ShowAnswerData(
        tag_ids=data.data.correct_tags,
        description_ids=data.data.correct_description_ids,
    )
    show_answer_message = ShowAnswerMessage(data=show_answer_data)
    await clients.broadcast(room_id, show_answer_message.model_dump())

    # 构建得分更新消息
    score_entries = [
        ScoreEntry(
            player_id=player_id,
            username=next(
                (p["username"] for p in players if p["id"] == player_id),
                f"Player {player_id}",
            ),
            score=player_scores.get(player_id, 0),
        ) for player_id in player_scores
    ]

    score_update_data = ScoreUpdateData(scores=score_entries)
    score_update_message = ScoreUpdateMessage(data=score_update_data)

    # 广播得分更新事件
    await clients.broadcast(room_id, score_update_message.model_dump())

    # 触发状态转换到 COMPLETED
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
                    build_round_state_update_message(RoundState.COMPLETED).model_dump(),
                )
                logger.info("Room %s round state transitioned to COMPLETED", room_id)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Failed to transition to COMPLETED: %s", e)

    # 广播回合结束事件
    round_end_message = RoundEndMessage()
    await clients.broadcast(room_id, round_end_message.model_dump())

    logger.info("Scoring completed for room %s and round marked as COMPLETED", room_id)
