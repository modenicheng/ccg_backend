from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from client_manager import ClientManager, Client
from db.session import AsyncSessionLocal
from utils import get_logger
from utils.enumerations import GameEventType
from db.models import RoomStatusORM
from . import regist
from sqlalchemy import select
from db import models
from cache import room_cache
from utils.ts import get_ts_ms
from utils.enumerations import EventType, GameEventType, ErrorEventType
from schemas.ws_messages.judge_schemas import *
from schemas.ws_messages.playback_schemas import *
from schemas.ws_messages.room_schemas import *
from schemas.ws_messages.round_event_schemas import *
import cache.schemas as cache_schemas
from cache.utils import room_manager
from db.crud import (
    get_current_song_info,
    get_player_answers_for_judging,
)

logger = get_logger(__name__)


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

        if room.status != RoomStatusORM.WAITING:
            logger.warning(
                "Received game start event for room %s which is not in waiting state",
                room_id
            )
            await client.send_error(GameEventType.GAME_START,
                                    "Room is not in waiting state")
            return
        room.status = RoomStatusORM.RUNNING
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


@regist(GameEventType.SUBMIT_ANSWER, SubmitAnswerMessage)
async def handle_submit_answer(
    data: SubmitAnswerMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **kwargs: Any
) -> None:
    """处理玩家提交答案事件
    
    将玩家答案保存到数据库，并广播给所有客户端
    """
    if client is None or room_id is None:
        return

    player_id = client.user.id
    player_username = client.user.username

    logger.info(
        "Player %s (id=%s) submitting answer in room %s",
        player_username, player_id, room_id
    )

    try:
        async with AsyncSessionLocal() as session:
            # 获取当前歌曲信息
            song_id, song_index = await get_current_song_info(session, room_id)
            if song_id is None or song_index is None:
                logger.error(
                    "Cannot determine current song for room %s", room_id)
                await client.send_error(
                    GameEventType.SUBMIT_ANSWER,
                    "Cannot determine current song")
                return

            # 创建玩家答案记录
            player_answer = models.PlayerAnswer(
                room_id=room_id,
                user_id=player_id,
                song_id=song_id,
                round_index=song_index,
                selected_tag_ids=data.data.selected_tag_ids,
                description_text=data.data.description_text,
                answer_order=None  # 抢答顺序在判分时设置
            )
            session.add(player_answer)
            await session.commit()

            logger.info(
                "Saved answer for player %s (id=%s) song %s round %s in room %s",
                player_username, player_id, song_id, song_index, room_id
            )

    except Exception as e:
        logger.error(
            "Failed to save player answer for player %s in room %s: %s",
            player_id, room_id, e
        )
        await client.send_error(
            GameEventType.SUBMIT_ANSWER,
            "Failed to save answer, please try again")
        return

    # 广播答案给所有玩家（ANSWER_BROADCAST事件）
    answer_broadcast_data = AnswerBroadcastData(
        player_id=str(player_id),
        selected_tag_ids=data.data.selected_tag_ids,
        description_text=data.data.description_text
    )
    answer_broadcast_message = AnswerBroadcastMessage(data=answer_broadcast_data)

    await clients.broadcast(
        room_id,
        answer_broadcast_message.model_dump()
    )

    logger.info(
        "Broadcasted answer from player %s (id=%s) in room %s",
        player_username, player_id, room_id
    )

    # 从抢答队列中移除当前玩家（已作答）
    await room_cache.remove_from_answer_queue(room_id, player_id)

    # 清空当前作答者
    await room_manager.set_current_answerer(room_id, "")

    # 获取下一个玩家（如果队列中还有玩家）
    next_queue = await room_cache.get_answer_queue(room_id)
    if next_queue:
        # 设置下一个玩家为当前作答者
        next_player = str(next_queue[0].player_id)
        await room_manager.set_current_answerer(room_id, next_player)
        logger.info("Next player in queue: %s", next_player)
    else:
        logger.info("Answer queue empty after submission in room %s", room_id)

    # 广播更新后的抢答队列
    answer_queue_data = AnswerQueueData(queue=next_queue)
    answer_queue_message = AnswerQueueMessage(data=answer_queue_data)
    await clients.broadcast(room_id, answer_queue_message.model_dump())
