"""WebSocket connection lifespan event handlers."""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import cache
import cache.schemas
from cache import room_cache
from client_manager import ClientManager, Client
from db import models
from schemas.ws_messages import room_schemas as RoomSchema
from schemas.ws_messages.room_schemas import AnswerQueueItem
from utils import get_logger

logger = get_logger(__name__)


async def on_connect(
    session: AsyncSession,
    cl: Client,
    clients: ClientManager,
    room_id: str,
) -> None:
    """
    Handle client connection to a room.

    This function is called when a client connects to a room. It performs the following operations:
    1. Updates the player's online status in the room cache
    2. Persists the online status to the database for the current session
    3. Fetches the current room state with associated users, tag groups, and scores
    4. Updates the room's player online status in cache
    5. Retrieves the current playback state and answer queue for the room
     6. Sends the room state to the connected client and broadcasts a player join message
        to other clients

    Args:
        session (AsyncSession): The async database session for executing queries and commits
        cl (Client): The client object representing the connected user
        clients (ClientManager): The client manager for broadcasting messages to multiple clients
        room_id (str): The ID of the room the client is connecting to

    Returns:
        None

    Raises:
        Logs a warning if the room is not found in the database during connection

    Note:
        - The function ensures database persistence of online status within the current session,
          as cl.user may be a cross-session object
        - Uses asyncio.gather to concurrently send messages to avoid blocking subsequent code
        - Excludes the connecting client from the broadcast join message
    """
    # pylint: disable=too-many-locals
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

    stmt = (select(models.Room).where(models.Room.id == room_id).options(
        selectinload(models.Room.users),
        selectinload(models.Room.tag_groups),
        selectinload(models.Room.scores),
    ))
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()
    if room is None:
        logger.warning("Room %s not found during on_connect for client %s", room_id, cl)
        return

    await room_cache.update_room_player_online_status(room_id, cl.user.id, True)
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
    res = await asyncio.gather(
        *[
            cl.ws.send_json(room_state_message.model_dump()),
            clients.broadcast(room_id, join_message.model_dump(),
                              excluded_clients={cl}),
        ],
        return_exceptions=True,
    )  # 让当前函数成为异步，避免阻塞后续代码

    for i, r in enumerate(res):
        if isinstance(r, Exception):
            logger.error(
                "Exception occurred in asyncio.gather task %d for client %s: %s",
                i,
                cl,
                r,
                exc_info=r,
            )
    logger.debug("Finished sending initial room state to client %s: %s", cl, res)


async def on_disconnect(
    session: AsyncSession,
    cl: Client,
    clients: ClientManager,
    room_id: str,
) -> None:
    """
    Handle client disconnection from a room.

    Closes the websocket connection, updates player status to offline,
    broadcasts a leave message to other clients in the room, and persists
    the offline status to the database and cache.

    Args:
        session: AsyncSession for database operations.
        cl: Client object representing the disconnected client.
        clients: ClientManager instance managing all connected clients.
        room_id: Unique identifier of the room the client is leaving.

    Returns:
        None
    """
    try:
        await cl.ws.close(code=1000, reason="Client disconnected")
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to close websocket for client %s", cl)

    player_item = cache.schemas.RoomStatePlayerItem.model_validate(cl.user)
    player_item.online = False

    leave_message = RoomSchema.PlayerLeaveMessage(
        data=RoomSchema.RoomStatePlayerItem.model_validate(player_item))
    await clients.broadcast(room_id, leave_message.model_dump(), excluded_clients={cl})

    user = select(models.User).where(models.User.id == cl.user.id)
    result = await session.execute(user)
    user_obj = result.scalar_one_or_none()
    if user_obj:
        user_obj.online = False
    await room_cache.set_room_player(room_id, player_item)

    clients.pop(room_id, cl)
