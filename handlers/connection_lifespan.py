import asyncio
from datetime import datetime, timezone
import random

from pydantic import ValidationError

from sqlalchemy.orm import selectinload

from cache.utils import room_manager
from cache import room_cache
import cache
import cache.schemas
from client_manager import ClientManager
from client_manager import Client
from schemas.base_message import *
from utils import get_logger
from utils.enumerations import EventType, GameEventType, ErrorEventType
from db.session import get_db, AsyncSessionLocal
from db import models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.ws_messages import room_schemas as RoomSchema
from utils.ts import get_ts_ms
from . import regist
from schemas.ws_messages.judge_schemas import *
from schemas.ws_messages.playback_schemas import *
from schemas.ws_messages.room_schemas import *
from schemas.ws_messages.round_event_schemas import *

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

    # 确保数据库中的在线状态在当前 session 内被持久化（cl.user 可能是跨 session 对象）
    user_stmt = select(models.User).where(models.User.id == cl.user.id)
    user_result = await session.execute(user_stmt)
    user_obj = user_result.scalar_one_or_none()
    if user_obj:
        user_obj.online = True
    await session.commit()

    stmt = select(models.Room).where(models.Room.id == room_id).options(
        selectinload(models.Room.users), selectinload(models.Room.tag_groups),
        selectinload(models.Room.scores))
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()
    if room is None:
        logger.warning("Room %s not found during on_connect for client %s",
                       room_id, cl)
        return

    await room_cache.update_room_player_online_status(room_id, cl.user.id,
                                                      True)
    # 连接时发送当前播放状态
    message = RoomSchema.ClientRoomState.model_validate(room)

    playback_state = await room_cache.get_room_playback_state(room_id)
    if playback_state:
        message.playback_status = RoomSchema.PlaybackState.model_validate(
            playback_state)
    queue: list[AnswerQueueItem] = await room_cache.get_answer_queue(room_id)
    message.answer_queue = queue

    room_state_message = RoomSchema.RoomStateMessage(data=message)
    join_message = RoomSchema.PlayerJoinMessage(
        data=RoomSchema.RoomStatePlayerItem.model_validate(player_item))
    res = await asyncio.gather(*[
        cl.ws.send_json(room_state_message.model_dump()),
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
    try:
        await cl.ws.close(code=1000, reason="Client disconnected")
    except Exception:
        logger.warning("Failed to close websocket for client %s", cl)

    player_item = cache.schemas.RoomStatePlayerItem.model_validate(cl.user)
    player_item.online = False
    user = select(models.User).where(models.User.id == cl.user.id)
    result = await session.execute(user)
    user_obj = result.scalar_one_or_none()
    if user_obj:
        user_obj.online = False
        await session.commit()
    await room_cache.set_room_player(room_id, player_item)
    cl.user.online = False  # 同步更新数据库在线状态（如果有这个字段的话）
    await room_cache.update_room_player_online_status(room_id, cl.user.id,
                                                      False)

