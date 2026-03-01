from client_manager import ClientManager, Client
from db.session import session_scope
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

logger = get_logger(__name__)


@regist(GameEventType.GAME_START, data_validator=GameStartMessage)
async def handle_game_start(
    data: GameStartMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
):
    logger.info(
        f"Handling game start event for room {room_id} with data: {data}")

    async with session_scope() as session:
        room = (await session.execute(
            select(models.Room).where(models.Room.id == room_id)
        )).scalar_one_or_none()

        if not room:
            logger.error(f"Room {room_id} not found")
            await client.send_error(GameEventType.GAME_START, "Room not found")
            return

        if room.status != RoomStatusORM.WAITING:
            logger.warning(
                f"Received game start event for room {room_id} which is not in waiting state"
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
):
    logger.info(
        f"Handling round end event for room {room_id} with data: {data}")
    if not client.user.is_owner:
        logger.warning(
            f"User {client.user.username} attempted to end round in room {room_id} but is not the owner"
        )
        await client.send_error(
            GameEventType.ROUND_END,
            "Only the room owner can end the round manually")
        return

    await clients.broadcast(room_id, data.model_dump())


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
    answer_item = cache_schemas.AnswerQueueItem(player_id=int(player_id),
                                                offset_ts=offset_ts,
                                                server_ts=server_ts,
                                                order=None,
                                                is_answering=False)
    try:
        result = await room_cache.append_attempt_answer_player(
            room_id, answer_item)
    except ValueError as ve:
        logger.warning(
            f"Player {player_id} attempted to answer in room {room_id} but is already in the queue"
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

        # # 广播PAUSE事件给所有客户端
        # # 构造PAUSE消息
        """
        ！！！直接前端控制暂停，后端不广播PAUSE事件了，避免网络延迟导致前端无法及时暂停
         前端在收到抢答事件时立即暂停播放
        """
        # pause_data = PlayControlData(progress_ms=current_progress,
        #                              offset_ts=offset_ts,
        #                              audio_url=None)
        # pause_message = PauseMessage(data=pause_data)

        # # 使用_safe_broadcast发送（排除发送者）
        # await clients.broadcast(room_id,
        #                         pause_message.model_dump(),
        #                         excluded_clients={client})

        # logger.info(
        #     "Paused playback and set current answerer to %s in room %s",
        #     player_id, room_id)

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
