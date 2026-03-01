import asyncio
import time
from datetime import datetime, timezone

from fastapi import WebSocket
from pydantic import ValidationError
from typing import Any

from sqlalchemy.orm import selectinload

from cache.utils import room_manager
from cache import room_cache
import cache
import cache.schemas
from client_manager import ClientManager
from client_manager import Client
from schemas.base_message import *
from schemas.ws_messages.judge_schemas import (
    JudgingMessage as JudgingMessageNew,
    JudgeSubmitMessage as JudgeSubmitMessageNew,
    ScoreUpdateMessage as ScoreUpdateMessageNew,
    ScoreEntry as ScoreEntryNew,
    ScoreUpdateData as ScoreUpdateDataNew,
)
from schemas.song import WebSocketErrorResponse
from schemas.ws_messages.error_schemas import WebSocketErrorEvent
from db.crud import (
    fetch_room_object,
    get_room_song_queue,
    get_current_song_info,
    get_player_answers_for_judging,
    get_tag_group_map,
    update_player_answer_order,
    save_score_record,
)
from utils import get_logger
from utils.enumerations import EventType, GameEventType, ErrorEventType
from db.session import get_db, AsyncSessionLocal
from db import models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.ws_messages import room_schemas as RoomSchema
from utils.ts import get_ts_ms
from . import regist
from schemas.ws_messages.game_event_schemas import *
from schemas.ws_messages.room_schemas import *

logger = get_logger(__name__)


async def on_connect(
    session: AsyncSession,
    cl: Client,
    clients: ClientManager,
    room_id: str,
):
    player_item = cache.schemas.RoomStatePlayerItem.model_validate(cl.user)
    player_item.online = True
    await room_cache.set_room_player(room_id, player_item)

    cl.user.online = True
    await session.commit()

    stmt = select(models.Room).where(models.Room.id == room_id).options(
        selectinload(models.Room.users), selectinload(models.Room.tag_groups))
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()

    await room_cache.update_room_player_online_status(room_id, cl.user.id,
                                                      True)
    # 连接时发送当前播放状态
    message = RoomSchema.ClientRoomState.model_validate(room)

    playback_state = await room_cache.get_room_playback_state(room_id)
    if playback_state:
        message.playback_status = RoomSchema.PlaybackState.model_validate(
            playback_state)
    join_message = RoomSchema.PlayerJoinMessage(
        data=RoomSchema.RoomStatePlayerItem.model_validate(cl.user))
    res = await asyncio.gather(*[
        cl.ws.send_json(message.model_dump()),
        clients.broadcast(room_id,
                          join_message.model_dump(),
                          excluded_clients={cl})
    ],
                               return_exceptions=True)  # 让当前函数成为异步，避免阻塞后续代码

    logger.debug("Finished sending initial room state to client %s: %s", cl,
                 res)


async def on_disconnect(
    session: AsyncSession,
    cl: Client,
    clients: ClientManager,
    room_id: str,
):
    player_item = cache.schemas.RoomStatePlayerItem.model_validate(cl.user)
    player_item.online = False
    await session.commit()
    await room_cache.set_room_player(room_id, player_item)
    cl.user.online = False  # 同步更新数据库在线状态（如果有这个字段的话）
    await room_cache.update_room_player_online_status(room_id, cl.user.id,
                                                      False)


@regist(GameEventType.JUDGING, JudgingMessage)
async def handle_judging(data: JudgingMessage, clients: ClientManager,
                         client: Client, room_id: str, **kwargs):
    if not isinstance(data, dict):
        await client.send_error(GameEventType.JUDGING, "Expected JSON object")
        return

    # 广播 JUDGING 事件给所有客户端
    await clients.broadcast(room_id, data, excluded_clients={client})


@regist(GameEventType.JUDGE_SUBMIT, JudgeSubmitMessageNew)
async def handle_judge_submit(data, clients: ClientManager, client: Client,
                              room_id: str, **kwargs):

    if not client.user.is_owner:
        await client.send_error(GameEventType.JUDGE_SUBMIT,
                                "Only owner can submit judge result")
        return

    # 如果选择跳过计分，直接结束
    if data.data.skip_scoring:
        logger.info("Skipping scoring for room %s", room_id)
        return

    # 使用数据库会话获取数据
    try:
        async with AsyncSessionLocal() as db:
            # 检查房间是否存在
            room = await fetch_room_object(db, room_id)
            if not room:
                await _safe_send_error(clients, cl, GameEventType.JUDGE_SUBMIT,
                                       "Room not found")
                return

            # 获取当前歌曲信息
            song_id, song_index = await get_current_song_info(db, room_id)
            if song_id is None or song_index is None:
                await _safe_send_error(clients, cl, GameEventType.JUDGE_SUBMIT,
                                       "Cannot determine current song")
                return

            # 获取房间玩家
            players = [{
                "id": str(user.id),
                "username": user.username
            } for user in room.users]

            # 获取抢答队列（从Redis暂时获取，后续可能需要移到数据库）
            # 使用 room_cache.get_answer_queue 获取 AnswerQueueItem 列表，然后提取玩家ID
            answer_queue_items = await room_cache.get_answer_queue(
                room_id)  # 已排序的 AnswerQueueItem 列表
            answer_queue = [
                str(item.player_id) for item in answer_queue_items
            ]  # 转换为玩家ID字符串列表

            # 从数据库获取玩家答案
            player_answers_raw = await get_player_answers_for_judging(
                db, room_id, song_id, song_index)

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

            # 初始化玩家得分
            player_scores = {player['id']: 0 for player in players}
            correct_tags = payload.data.correct_tags.copy()  # 创建副本以避免修改原始数据

            # 按抢答顺序遍历玩家
            for player_id in answer_queue:
                if player_id not in player_answers:
                    continue

                player_answer = player_answers[player_id]
                selected_tags = player_answer.get('selected_tag_ids', [])

                # 检查每个标签组
                for tag_group_id, group_tags in tag_group_map.items():
                    # 找到该标签组的正确答案
                    group_correct_tags = [
                        tag for tag in correct_tags if tag in group_tags
                    ]
                    if not group_correct_tags:
                        continue

                    # 检查玩家是否选择了该标签组的正确答案
                    player_group_tags = [
                        tag for tag in selected_tags if tag in group_tags
                    ]
                    if player_group_tags == group_correct_tags:
                        # 给玩家加1分
                        player_scores[player_id] += 1
                        # 从正确标签中移除该标签组的标签，避免重复计分
                        for tag in group_correct_tags:
                            if tag in correct_tags:
                                correct_tags.remove(tag)
                        # 终止当前标签组的遍历
                        break

            # 处理精准描述计分
            correct_description_ids = payload.data.correct_description_ids
            if correct_description_ids:
                for player_id in answer_queue:
                    if player_id not in player_answers:
                        continue

                    player_answer = player_answers[player_id]
                    player_description_text = player_answer.get(
                        'description_text')

                    # 这里需要检查描述文本是否匹配正确的描述ID
                    # 由于前端传的是description_id，但数据库存的是description_text
                    # 这里简化处理：如果玩家有描述文本，则给1分（实际应根据描述匹配逻辑）
                    # TODO: 实现准确的描述匹配逻辑
                    if player_description_text and player_description_text.strip(
                    ):
                        # 给玩家加1分（简化逻辑）
                        player_scores[player_id] += 1
                        # 终止精准描述的遍历
                        break

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
        await _safe_send_error(clients, cl, GameEventType.JUDGE_SUBMIT,
                               f"Internal server error: {str(e)}")
        return

    # 计分完成，清空抢答队列（Redis部分）
    await room_cache.clear_answer_queue(room_id)

    # 构建得分更新消息
    score_entries = [
        ScoreEntryNew(player_id=player_id,
                      username=next((p['username']
                                     for p in players if p['id'] == player_id),
                                    f"Player {player_id}"),
                      score=player_scores.get(player_id, 0))
        for player_id in player_scores
    ]

    score_update_data = ScoreUpdateDataNew(scores=score_entries)
    score_update_message = ScoreUpdateMessageNew(data=score_update_data)

    # 广播得分更新事件
    await clients.broadcast(room_id,
                            score_update_message.model_dump(),
                            excluded_clients={client})

    logger.info("Scoring completed for room %s", room_id)


@regist(GameEventType.ATTEMPT_ANSWER, AttemptAnswerMessage)
async def handle_attempt_answer(data: AttemptAnswerMessage,
                                clients: ClientManager, client: Client,
                                room_id: str, **kwargs):

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
    answer_item = cache.schemas.AnswerQueueItem(player_id=int(player_id),
                                                offset_ts=offset_ts,
                                                server_ts=server_ts,
                                                order=None,
                                                is_answering=False)
    result = await room_cache.append_attempt_answer_player(
        room_id, answer_item)

    if result is None:
        logger.error("Failed to add player %s to answer queue in room %s",
                     player_id, room_id)
        # 可以发送错误，但暂时只记录日志
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

        # 广播PAUSE事件给所有客户端
        # 构造PAUSE消息
        pause_data = PlayControlData(progress_ms=current_progress,
                                     offset_ts=offset_ts,
                                     audio_url=None)
        pause_message = PauseMessage(data=pause_data)

        # 使用_safe_broadcast发送（排除发送者）
        await clients.broadcast(room_id, pause_message.model_dump(),
                                excluded_clients={client})

        logger.info(
            "Paused playback and set current answerer to %s in room %s",
            player_id, room_id)

    # 获取更新后的排序队列 - 使用 room_cache.get_answer_queue
    sorted_queue = await room_cache.get_answer_queue(room_id)

    # 广播ATTEMPT_ANSWER事件给其他客户端（保持原有行为）
    await clients.broadcast(
        room_id,
        payload.model_dump(),
        excluded_clients={client},
    )

    # 广播ANSWER_QUEUE事件给所有客户端（包括发送者）
    queue_entries = []
    for entry in sorted_queue:
        # 将服务器时间戳转换为ISO格式字符串作为added_at
        added_at_str = datetime.fromtimestamp(
            entry.server_ts / 1000,
            timezone.utc).isoformat().replace("+00:00", "Z")
        queue_entries.append(
            AnswerQueueEntry(player_id=str(entry.player_id),
                             offset_ts=entry.offset_ts,
                             server_ts=entry.server_ts,
                             added_at=added_at_str))

    answer_queue_data = AnswerQueueData(queue=queue_entries)
    answer_queue_message = AnswerQueueMessage(data=answer_queue_data)

    await clients.broadcast(
        room_id,
        answer_queue_message.model_dump(),
    )

    logger.info("Answer queue updated in room %s: %s", room_id, sorted_queue)


@regist(GameEventType.SUBMIT_ANSWER)
async def handle_submit_answer(data,
                               clients: ClientManager,
                               websocket=None,
                               room_id=None,
                               **kwargs):
    """处理玩家提交答案事件"""
    if not isinstance(data, dict):
        await _safe_send_error(clients, websocket, GameEventType.SUBMIT_ANSWER,
                               "Expected JSON object")
        return

    if websocket is None or room_id is None:
        return

    # 验证当前玩家是否为当前作答者
    user = getattr(websocket.state, 'user', None)
    if not user:
        await _safe_send_error(clients, websocket, GameEventType.SUBMIT_ANSWER,
                               "User not authenticated")
        return

    player_id = str(user.id)
    current_answerer = await room_manager.get_current_answerer(room_id)
    if current_answerer != player_id:
        await _safe_send_error(clients, websocket, GameEventType.SUBMIT_ANSWER,
                               "Not your turn to answer")
        return

    try:
        payload = SubmitAnswerMessage.model_validate(data)
    except ValidationError as exc:
        logger.warning("Invalid SUBMIT_ANSWER payload: %s", exc)
        await _safe_send_error(clients, websocket, GameEventType.SUBMIT_ANSWER,
                               f"Invalid payload: {exc.errors()}")
        return

    # 将答案保存到数据库（PlayerAnswer表）
    try:
        async with AsyncSessionLocal() as db:
            # 获取当前歌曲ID和轮次索引
            song_index = await room_manager.get_current_song_index(room_id)
            song_queue = await room_manager.get_song_queue(room_id)
            if song_index is not None and song_queue and 0 <= song_index < len(
                    song_queue):
                song_id = int(song_queue[song_index])
                # 创建玩家答案记录
                player_answer = models.PlayerAnswer(
                    room_id=room_id,
                    user_id=user.id,
                    song_id=song_id,
                    round_index=song_index,  # 使用歌曲索引作为轮次索引
                    selected_tag_ids=payload.data.selected_tag_ids,
                    description_text=payload.data.description_text,
                    answer_order=None  # 抢答顺序已在handle_judge_submit中设置
                )
                db.add(player_answer)
                await db.commit()
                logger.info("Saved answer for player %s song %d in room %s",
                            player_id, song_id, room_id)
            else:
                logger.warning(
                    "Cannot determine current song for room %s, skipping answer save",
                    room_id)
    except Exception as e:
        logger.error("Failed to save player answer to database: %s", e)
        # 继续执行，仍然广播答案

    # 广播答案给所有玩家（ANSWER_BROADCAST事件）
    answer_broadcast_data = AnswerBroadcastData(
        player_id=player_id,
        selected_tag_ids=payload.data.selected_tag_ids,
        description_text=payload.data.description_text)
    answer_broadcast_message = AnswerBroadcastMessage(
        data=answer_broadcast_data)
    await clients.broadcast(room_id, answer_broadcast_message.model_dump())

    # 从抢答队列中移除当前玩家（已作答）
    # 使用 room_cache.remove_from_answer_queue，需要将 player_id 转换为 int
    await room_cache.remove_from_answer_queue(room_id, int(player_id))

    # 清空当前作答者
    await room_manager.set_current_answerer(room_id, "")

    # 获取下一个玩家（如果队列中还有玩家）
    # 使用 room_cache.get_answer_queue 获取排序后的 AnswerQueueItem 列表
    next_queue = await room_cache.get_answer_queue(room_id)
    if next_queue:
        # 设置下一个玩家为当前作答者
        next_player = str(next_queue[0].player_id)
        await room_manager.set_current_answerer(room_id, next_player)
        # 发送YOUR_TURN给下一个玩家
        # 需要获取下一个玩家的WebSocket，但这里无法直接获取
        # 暂时跳过，前端可以通过ANSWER_QUEUE事件知道轮到谁
        logger.info("Next player in queue: %s", next_player)
    else:
        # 队列为空，恢复播放？
        # 暂时不处理，由房主控制
        logger.info("Answer queue empty after submission in room %s", room_id)

    # 广播更新后的抢答队列
    queue_entries = []
    for entry in next_queue:
        # 将服务器时间戳转换为ISO格式字符串作为added_at
        added_at_str = datetime.fromtimestamp(
            entry.server_ts / 1000,
            timezone.utc).isoformat().replace("+00:00", "Z")
        queue_entries.append(
            AnswerQueueEntry(player_id=str(entry.player_id),
                             offset_ts=entry.offset_ts,
                             server_ts=entry.server_ts,
                             added_at=added_at_str))
    answer_queue_data = AnswerQueueData(queue=queue_entries)
    answer_queue_message = AnswerQueueMessage(data=answer_queue_data)
    await clients.broadcast(room_id, answer_queue_message.model_dump())

    logger.info("Answer submitted by player %s in room %s", player_id, room_id)
