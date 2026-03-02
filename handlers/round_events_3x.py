# Standard library imports
import random
from datetime import datetime, timezone

# Third-party imports
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any
# Local imports
from client_manager import ClientManager, Client
from db.session import session_scope
from db import models
from db.crud import (
    get_player_answers_for_judging,
    get_tag_group_map,
    update_player_answer_order,
    save_score_record,
    get_current_song_info,
    fetch_room_object,
)
from utils import get_logger
from utils.enumerations import GameEventType, EventType, ErrorEventType
from utils.ts import get_ts_ms
from . import regist
from cache import room_cache
import cache.schemas as cache_schemas
from cache.utils import room_manager
from schemas.ws_messages.judge_schemas import *
from schemas.ws_messages.playback_schemas import *
from schemas.ws_messages.room_schemas import *
from schemas.ws_messages.round_event_schemas import *

logger = get_logger(__name__)


def calculate_player_scores(
    answer_queue: list[str],
    player_answers: dict[str, dict[str, object]],
    tag_group_map: dict[int, list[int]],
    correct_tags: list[int],
    correct_description_ids: list[int],
) -> dict[str, int]:
    """Calculate player scores based on answer queue and correct answers.

    Args:
        answer_queue: List of player IDs in answer order (strings)
        player_answers: Dict mapping player_id to answer data
        tag_group_map: Dict mapping tag_group_id to list of tag IDs
        correct_tags: List of correct tag IDs
        correct_description_ids: List of player IDs with correct descriptions

    Returns:
        Dict mapping player_id to score delta for this round
    """
    player_scores = {}
    # Initialize scores for all players in answer queue
    for player_id in answer_queue:
        player_scores[player_id] = 0

    # Create a copy of correct_tags to modify as we award points
    remaining_correct_tags = correct_tags.copy()

    # Tag group scoring: for each player in answer order
    for player_id in answer_queue:
        if player_id not in player_answers:
            continue

        player_answer = player_answers[player_id]
        selected_tags = player_answer.get('selected_tag_ids', [])

        # Check each tag group
        for tag_group_id, group_tags in tag_group_map.items():
            # Find correct tags in this group
            group_correct_tags = [
                tag for tag in remaining_correct_tags if tag in group_tags
            ]
            if not group_correct_tags:
                continue

            # Check if player selected exactly the correct tags for this group
            player_group_tags = [
                tag for tag in selected_tags if tag in group_tags
            ]
            if player_group_tags == group_correct_tags:
                # Award 1 point
                player_scores[player_id] += 1
                # Remove these tags from consideration
                for tag in group_correct_tags:
                    if tag in remaining_correct_tags:
                        remaining_correct_tags.remove(tag)
                # Move to next player (only one point per player from tag groups)
                break

    # Description scoring: for each player in answer order
    if correct_description_ids:
        for player_id in answer_queue:
            if player_id not in player_answers:
                continue

            # Check if player is in correct_description_ids
            if int(player_id) in correct_description_ids:
                player_scores[player_id] += 1
                # Only first matching player gets description point
                break

    return player_scores


@regist(GameEventType.GAME_START, data_validator=GameStartMessage)
async def handle_game_start(
    data: GameStartMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    logger.info(
        "Handling game start event for room %s", room_id)

    async with AsyncSessionLocal() as session:
        room = (await session.execute(
            select(models.Room).where(models.Room.id == room_id)
        )).scalar_one_or_none()

        if not room:
            logger.error("Room %s not found", room_id)
            await client.send_error(GameEventType.GAME_START, "Room not found")
            return

        if room.status != models.RoomStatusORM.WAITING:
            logger.warning(
                "Received game start event for room %s which is not in waiting state",
                room_id
            )
            await client.send_error(GameEventType.GAME_START,
                                    "Room is not in waiting state")
            return
        room.status = models.RoomStatusORM.RUNNING
        await session.commit()
        await clients.broadcast(room_id, data.model_dump())


@regist(GameEventType.ROUND_END, data_validator=RoundEndMessage)
async def handle_round_end(
    data: RoundEndMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    logger.info(
        "Handling round end event for room %s", room_id)
    if not client.user.is_owner:
        logger.warning(
            "User %s attempted to end round in room %s but is not the owner",
            client.user.username, room_id
        )
        await client.send_error(
            GameEventType.ROUND_END,
            "Only the room owner can end the round manually")
        return

    await clients.broadcast(room_id, data.model_dump())


@regist(GameEventType.ATTEMPT_ANSWER, AttemptAnswerMessage)
async def handle_attempt_answer(
    data: AttemptAnswerMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **kwargs: Any
) -> None:
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
        is_answering=False
    )
    try:
        result = await room_cache.append_attempt_answer_player(
            room_id, answer_item)
    except ValueError:
        logger.warning(
            "Player %s attempted to answer in room %s but is already in the queue",
            player_id, room_id
        )
        await client.send_error(
            GameEventType.ATTEMPT_ANSWER.value,
            "You have already attempted to answer, please wait for next round")
        return

    if result is None:
        logger.error("Failed to add player %s to answer queue in room %s",
                     player_id, room_id)
        await client.send_error(
            GameEventType.ATTEMPT_ANSWER.value,
            "Failed to join answer queue, please try again")
        return

    logger.info(
        "Player %s attempted to answer in room %s at offset %d ms (server ts: %d)",
        player_id, room_id, offset_ts, server_ts)

    # 如果此前队列为空，暂停播放并设置当前作答玩家
    if was_empty:
        # 获取当前播放进度（从Redis房间状态）
        current_progress = await room_manager.get_play_progress(room_id)

        # 更新Redis播放状态
        await room_manager.update_playback_state(
            room_id=room_id,
            round_state="paused",
            progress_ms=current_progress,
            offset_ts=offset_ts,  # 使用抢答时间戳作为offset_ts
            audio_url=None,
            event_ts=server_ts,
            event_name="PAUSE")

        # 设置当前作答玩家
        await room_manager.set_current_answerer(room_id, player_id)

        # 发送YOUR_TURN事件给当前作答玩家
        your_turn_data = YourTurnData()
        your_turn_message = YourTurnMessage(data=your_turn_data)
        await client.send(your_turn_message.model_dump())
        logger.info("Sent YOUR_TURN to player %s in room %s", player_id,
                    room_id)

    # 获取更新后的排序队列 - 使用 room_cache.get_answer_queue
    sorted_queue: list[AnswerQueueItem] = await room_cache.get_answer_queue(
        room_id)

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


@regist(GameEventType.JUDGING, JudgingMessage)
async def handle_judging(data: JudgingMessage, clients: ClientManager,
                         client: Client, room_id: str, **kwargs):
    """处理进入判分环节事件"""
    try:
        async with session_scope() as db:
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
                await client.send_error(GameEventType.JUDGING,
                                        "Song not found")
                return

            # 获取历史标签
            tag_history_stmt = select(models.SongTagHistory.tag_id).where(
                models.SongTagHistory.song_id == song_id)
            tag_history_result = await db.execute(tag_history_stmt)
            history_tag_ids = [
                tag_id[0] for tag_id in tag_history_result.all()
            ]

            # 获取参考精确描述（正确答案）
            description_history_stmt = select(
                models.SongDescriptionHistory.description_text).where(
                    models.SongDescriptionHistory.song_id == song_id,
                    models.SongDescriptionHistory.is_correct == True)
            description_history_result = await db.execute(
                description_history_stmt)
            reference_descriptions = [
                desc[0] for desc in description_history_result.all()
            ]

            # 随机选择一个或多个参考描述
            if reference_descriptions:
                # 随机选择1-3个描述
                num_descriptions = min(random.randint(1, 3),
                                       len(reference_descriptions))
                reference_descriptions = random.sample(reference_descriptions,
                                                       num_descriptions)

            # 获取玩家答案（用于显示抢答者的精确描述）
            player_answers = await get_player_answers_for_judging(
                db, room_id, song_id, song_index)

            if not player_answers:
                logger.warning("No player answers found for judging in room %s, song %s", room_id, song_id)

            # 构建玩家描述列表
            player_descriptions = []
            for user_id, answer_data in player_answers.items():
                if answer_data['description_text']:
                    # 获取用户名
                    user_stmt = select(
                        models.User.username).where(models.User.id == user_id)
                    user_result = await db.execute(user_stmt)
                    username = user_result.scalar_one_or_none(
                    ) or f"Player {user_id}"

                    player_descriptions.append({
                        'id':
                        user_id,
                        'username':
                        username,
                        'description':
                        answer_data['description_text']
                    })

            # 构建歌曲信息
            song_info = SongInfo(
                title=song.title,
                artist=song.artist,
                album=song.album_name,
                cover_url=song.cover_url,
                platform_url=song.metadata_json.get('platform_url')
                if song.metadata_json else None)

            # 构建JUDGING事件数据
            judging_data = JudgingData(
                song=song_info,
                history_tag_ids=history_tag_ids,
                reference_descriptions=reference_descriptions,
                player_descriptions=player_descriptions)

            # 广播JUDGING事件给所有客户端
            judging_message = JudgingMessage(data=judging_data)
            await clients.broadcast(room_id, judging_message.model_dump())

            logger.info("Sent JUDGING event for room %s, song %s", room_id,
                        song.title)

    except Exception as e:
        logger.error("Error during JUDGING event: %s", e)
        await client.send_error(GameEventType.JUDGING,
                                f"Internal server error: {str(e)}")


@regist(GameEventType.JUDGE_SUBMIT, JudgeSubmitMessage)
async def handle_judge_submit(data: JudgeSubmitMessage, clients: ClientManager,
                              client: Client, room_id: str, **kwargs):
    """处理房主提交正确答案事件"""
    # 验证房主身份
    if not client.user.is_owner:
        await client.send_error(GameEventType.JUDGE_SUBMIT,
                                "Only owner can submit judge result")
        return

    # 如果选择跳过计分，直接结束
    if data.data.skip_scoring:
        logger.info("Skipping scoring for room %s", room_id)
        # 广播ROUND_END事件
        round_end_message = RoundEndMessage()
        await clients.broadcast(room_id, round_end_message.model_dump())
        return

    # 使用数据库会话获取数据
    try:
        async with session_scope() as db:
            # 检查房间是否存在
            room = await fetch_room_object(db, room_id)
            if not room:
                await client.send_error(GameEventType.JUDGE_SUBMIT,
                                        "Room not found")
                return

            # 获取当前歌曲信息
            song_id, song_index = await get_current_song_info(db, room_id)
            if song_id is None or song_index is None:
                await client.send_error(GameEventType.JUDGE_SUBMIT,
                                        "Cannot determine current song")
                return

            # 存储标签历史
            for tag_id in data.data.correct_tags:
                # 检查是否已存在
                existing_stmt = select(models.SongTagHistory).where(
                    models.SongTagHistory.song_id == song_id,
                    models.SongTagHistory.tag_id == tag_id)
                existing_result = await db.execute(existing_stmt)
                existing = existing_result.scalar_one_or_none()

                if not existing:
                    # 创建新记录
                    tag_history = models.SongTagHistory(
                        song_id=song_id,
                        tag_id=tag_id,
                        judged_by_user_id=client.user.id,
                        room_id=room_id)
                    db.add(tag_history)

            # 存储描述历史
            for description_id in data.data.correct_description_ids:
                # 获取玩家答案
                answer_stmt = select(models.PlayerAnswer).where(
                    models.PlayerAnswer.room_id == room_id,
                    models.PlayerAnswer.song_id == song_id,
                    models.PlayerAnswer.round_index == song_index,
                    models.PlayerAnswer.user_id == description_id)
                answer_result = await db.execute(answer_stmt)
                answer = answer_result.scalar_one_or_none()

                if answer and answer.description_text:
                    # 创建新记录
                    description_history = models.SongDescriptionHistory(
                        song_id=song_id,
                        description_text=answer.description_text,
                        is_correct=True,
                        judged_by_user_id=client.user.id,
                        room_id=room_id)
                    db.add(description_history)

            # 处理新的正确描述
            for description_text in data.data.new_correct_descriptions:
                if description_text.strip():
                    description_history = models.SongDescriptionHistory(
                        song_id=song_id,
                        description_text=description_text.strip(),
                        is_correct=True,
                        judged_by_user_id=client.user.id,
                        room_id=room_id)
                    db.add(description_history)

            # 获取房间玩家
            players = [{
                "id": str(user.id),
                "username": user.username
            } for user in room.users]

            # 获取抢答队列（从Redis暂时获取，后续可能需要移到数据库）
            answer_queue_items = await room_cache.get_answer_queue(
                room_id)  # 已排序的 AnswerQueueItem 列表
            answer_queue = [
                str(item.player_id) for item in answer_queue_items
            ]  # 转换为玩家ID字符串列表

            if not answer_queue:
                logger.warning("Empty answer queue for judging in room %s", room_id)

            # 从数据库获取玩家答案
            player_answers_raw = await get_player_answers_for_judging(
                db, room_id, song_id, song_index)

            if not player_answers_raw:
                logger.warning("No player answers found for judging in room %s, song %s", room_id, song_id)

            # 转换格式以便与answer_queue匹配（answer_queue中的player_id是字符串）
            player_answers = {}
            for user_id_int, answer_data in player_answers_raw.items():
                player_id_str = str(user_id_int)
                player_answers[player_id_str] = {
                    'selected_tag_ids': answer_data['selected_tag_ids'],
                    'description_text': answer_data['description_text'],
                    'answer_order': answer_data['answer_order']
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
                if player['id'] not in player_scores:
                    player_scores[player['id']] = 0

            # 更新数据库中的抢答顺序（answer_order）
            updated_count = await update_player_answer_order(
                db, room_id, song_id, song_index, answer_queue)
            logger.info("Updated answer_order for %d players in room %s",
                        updated_count, room_id)

            # 保存得分记录到数据库
            for player_id_str, score_delta in player_scores.items():
                if score_delta > 0:
                    try:
                        user_id = int(player_id_str)
                        await save_score_record(db, room_id, user_id,
                                                song_index, score_delta)
                    except (ValueError, Exception) as e:
                        logger.error("Failed to save score for player %s: %s",
                                     player_id_str, e)

            await db.commit()
            logger.info("Saved scoring results to database for room %s",
                        room_id)

    except Exception as e:
        logger.error("Error during judge submission for room %s: %s", room_id,
                     e)
        await client.send_error(GameEventType.JUDGE_SUBMIT.value,
                                f"Internal server error: {str(e)}")
        return

    # 计分完成，清空抢答队列（Redis部分）
    await room_cache.clear_answer_queue(room_id)

    # 构建得分更新消息
    score_entries = [
        ScoreEntry(player_id=player_id,
                   username=next((p['username']
                                  for p in players if p['id'] == player_id),
                                 f"Player {player_id}"),
                   score=player_scores.get(player_id, 0))
        for player_id in player_scores
    ]

    score_update_data = ScoreUpdateData(scores=score_entries)
    score_update_message = ScoreUpdateMessage(data=score_update_data)

    # 广播得分更新事件
    await clients.broadcast(room_id, score_update_message.model_dump())

    # 广播回合结束事件
    round_end_message = RoundEndMessage()
    await clients.broadcast(room_id, round_end_message.model_dump())

    logger.info("Scoring completed for room %s", room_id)



@regist(GameEventType.SUBMIT_ANSWER, SubmitAnswerMessage)
async def handle_submit_answer(
    data: SubmitAnswerMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **kwargs
):
    """处理玩家提交答案事件"""
    # 验证当前玩家是否为当前作答者
    player_id = str(client.user.id)
    current_answerer = await room_manager.get_current_answerer(room_id)
    if current_answerer != player_id:
        await client.send_error(
            GameEventType.SUBMIT_ANSWER,
            "Not your turn to answer"
        )
        return

    # 将答案保存到数据库（PlayerAnswer表）
    try:
        async with session_scope() as db:
            # 获取当前歌曲信息
            song_id, song_index = await get_current_song_info(db, room_id)
            if song_id is None or song_index is None:
                logger.warning(
                    "Cannot determine current song for room %s, skipping answer save",
                    room_id
                )
                await client.send_error(
                    GameEventType.SUBMIT_ANSWER,
                    "Cannot determine current song"
                )
                return

            # 创建玩家答案记录
            player_answer = models.PlayerAnswer(
                room_id=room_id,
                user_id=client.user.id,
                song_id=song_id,
                round_index=song_index,  # 使用歌曲索引作为轮次索引
                selected_tag_ids=data.data.selected_tag_ids,
                description_text=data.data.description_text,
                answer_order=None  # 抢答顺序已在handle_judge_submit中设置
            )
            db.add(player_answer)
            await db.commit()
            logger.info(
                "Saved answer for player %s song %d in room %s",
                player_id, song_id, room_id
            )
    except Exception as e:
        logger.error("Failed to save player answer to database: %s", e)
        await client.send_error(
            GameEventType.SUBMIT_ANSWER,
            f"Failed to save answer: {str(e)}"
        )
        return

    # 广播答案给所有玩家（ANSWER_BROADCAST事件）
    answer_broadcast_data = AnswerBroadcastData(
        player_id=player_id,
        selected_tag_ids=data.data.selected_tag_ids,
        description_text=data.data.description_text
    )
    answer_broadcast_message = AnswerBroadcastMessage(data=answer_broadcast_data)
    await clients.broadcast(room_id, answer_broadcast_message.model_dump())

    # 玩家提交答案后不清除队列，只更新当前作答者状态
    # 注意：玩家保持在队列中，直到回合结束才清空队列
    await room_manager.set_current_answerer(room_id, "")

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
            await room_manager.set_current_answerer(room_id, next_player)

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
                logger.info("Sent YOUR_TURN to next player %s in room %s", next_player, room_id)
            else:
                logger.warning("Could not find WebSocket for next player %s in room %s", next_player, room_id)
            logger.info("Next player in queue: %s", next_player)
        else:
            # 没有下一个玩家，清空当前作答者
            await room_manager.set_current_answerer(room_id, "")
            logger.info("No next player in queue after player %s submission in room %s", player_id, room_id)
    else:
        # 队列为空，恢复播放？
        # 暂时不处理，由房主控制
        logger.info("Answer queue empty after submission in room %s", room_id)

    # 广播更新后的抢答队列（使用与handle_attempt_answer相同的格式）
    answer_queue_data = AnswerQueueData(queue=current_queue)
    answer_queue_message = AnswerQueueMessage(data=answer_queue_data)
    await clients.broadcast(room_id, answer_queue_message.model_dump())

    logger.info("Answer submitted by player %s in room %s", player_id, room_id)
