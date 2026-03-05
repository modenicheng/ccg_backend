# Standard library imports
import asyncio
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
from db.crud import get_current_song_info
from utils import get_logger, generate_audio_token, get_audio_stream_url
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


@regist(GameEventType.GAME_START, data_validator=GameStartMessage)
async def handle_game_start(
    data: GameStartMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    logger.info("Handling game start event for room %s", room_id)

    async with session_scope() as session:
        room = (await session.execute(
            select(models.Room).where(models.Room.id == room_id).options(
                selectinload(models.Room.room_songs).selectinload(
                    models.RoomSong.song)
            )
        )).scalar_one_or_none()

        if not room:
            logger.error("Room %s not found", room_id)
            await client.send_error(GameEventType.GAME_START, "Room not found")
            return

        if room.status != models.RoomStatusORM.WAITING:
            logger.warning(
                "Received game start event for room %s which is not in waiting state",
                room_id)
            await client.send_error(GameEventType.GAME_START,
                                    "Room is not in waiting state")
            return

        if room.room_songs:
            room.status = models.RoomStatusORM.RUNNING
            await session.commit()
            await clients.broadcast(room_id, data.model_dump())

            # 生成临时音频访问令牌
            song = room.room_songs[0].song
            audio_token = await generate_audio_token(song.id)
            audio_url = get_audio_stream_url(audio_token)

            # 立即发送第一个回合开始事件
            room.current_song_index = 0
            playback_state = cache_schemas.PlaybackState(
                play_state="playing",
                progress_ms=0,
                offset_ts=0,
                audio_url=audio_url,
            )
            tasks = [
                clients.broadcast(
                    room_id,
                    RoundStartMessage(data=RoundStartData(
                        round_index=0,
                        audio_url=audio_url,
                        start_pertent=0.0)).model_dump()),
                session.commit(),
                room_cache.set_room_playback_state(room_id, playback_state),
            ]
            # 使用 asyncio.gather 来并行执行广播和数据库提交
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            logger.warning("Room %s has no songs when starting game", room_id)
            await clients.broadcast_error(
                room_id, ErrorEventType.HANDLER_EXCEPTION.value,
                "Cannot start game: no songs in room")


@regist(GameEventType.ROUND_END, data_validator=RoundEndMessage)
async def handle_round_end(
    data: RoundEndMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    logger.info("Handling round end event for room %s", room_id)
    if not client.user.is_owner:
        logger.warning(
            "User %s attempted to end round in room %s but is not the owner",
            client.user.username, room_id)
        await client.send_error(
            GameEventType.ROUND_END,
            "Only the room owner can end the round manually")
        return

    await clients.broadcast(room_id, data.model_dump())


@regist(GameEventType.ATTEMPT_ANSWER, AttemptAnswerMessage)
async def handle_attempt_answer(data: AttemptAnswerMessage,
                                clients: ClientManager, client: Client,
                                room_id: str, **kwargs: Any) -> None:
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
    answer_item = cache_schemas.AnswerQueueItem(player_id=int(player_id),
                                                offset_ts=offset_ts,
                                                server_ts=server_ts,
                                                order=None,
                                                is_answering=False)
    try:
        result = await room_cache.append_attempt_answer_player(
            room_id, answer_item)
    except ValueError:
        logger.warning(
            "Player %s attempted to answer in room %s but is already in the queue",
            player_id, room_id)
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
async def handle_submit_answer(data: SubmitAnswerMessage,
                               clients: ClientManager, client: Client,
                               room_id: str, **kwargs):
    """处理玩家提交答案事件"""
    # 验证当前玩家是否为当前作答者
    player_id = str(client.user.id)
    current_answerer = await room_manager.get_current_answerer(room_id)
    if current_answerer != player_id:
        await client.send_error(GameEventType.SUBMIT_ANSWER,
                                "Not your turn to answer")
        return

    # 将答案保存到数据库（PlayerAnswer表）
    try:
        async with session_scope() as db:
            # 获取当前歌曲信息
            song_id, song_index = await get_current_song_info(db, room_id)
            if song_id is None or song_index is None:
                logger.warning(
                    "Cannot determine current song for room %s, skipping answer save",
                    room_id)
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
                answer_order=None  # 抢答顺序已在handle_judge_submit中设置
            )
            db.add(player_answer)
            await db.commit()
            logger.info("Saved answer for player %s song %d in room %s",
                        player_id, song_id, room_id)
    except Exception as e:
        logger.error("Failed to save player answer to database: %s", e)
        await client.send_error(GameEventType.SUBMIT_ANSWER,
                                f"Failed to save answer: {str(e)}")
        return

    # 广播答案给所有玩家（ANSWER_BROADCAST事件）
    answer_broadcast_data = AnswerBroadcastData(
        player_id=player_id,
        selected_tag_ids=data.data.selected_tag_ids,
        description_text=data.data.description_text)
    answer_broadcast_message = AnswerBroadcastMessage(
        data=answer_broadcast_data)
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

        if current_player_index >= 0 and current_player_index + 1 < len(
                current_queue):
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
                logger.info("Sent YOUR_TURN to next player %s in room %s",
                            next_player, room_id)
            else:
                logger.warning(
                    "Could not find WebSocket for next player %s in room %s",
                    next_player, room_id)
            logger.info("Next player in queue: %s", next_player)
        else:
            # 没有下一个玩家，清空当前作答者
            await room_manager.set_current_answerer(room_id, "")
            logger.info(
                "No next player in queue after player %s submission in room %s",
                player_id, room_id)
    else:
        # 队列为空，恢复播放？
        # 暂时不处理，由房主控制
        logger.info("Answer queue empty after submission in room %s", room_id)

    # 广播更新后的抢答队列（使用与handle_attempt_answer相同的格式）
    answer_queue_data = AnswerQueueData(queue=current_queue)
    answer_queue_message = AnswerQueueMessage(data=answer_queue_data)
    await clients.broadcast(room_id, answer_queue_message.model_dump())

    logger.info("Answer submitted by player %s in room %s", player_id, room_id)
