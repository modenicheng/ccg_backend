"""WebSocket connection lifespan event handlers."""

from __future__ import annotations

import asyncio

from fastapi import WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.websockets import WebSocketState

import cache
import cache.schemas
from cache import room_cache
from cache.room_state_manager import RoomStateManager
from client_manager import ClientManager, Client
from db import crud, models
from handlers.registe_manager import regist
from schemas.ws_messages import room_schemas as RoomSchema
from schemas.ws_messages.room_schemas import (
    AnswerQueueItem,
    ClearAnswerQueueData,
    GameOverData,
    StartPosUpdateData,
)
from utils import get_logger
from utils.enumerations import GameEventType

logger = get_logger(__name__)


async def on_connect(  # pylint: disable=too-many-statements
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

    # 检查是否是观战者用户（id为0）
    is_spectator = cl.user.id == 0

    # 只有非观战者用户才更新缓存和数据库状态
    if not is_spectator:
        # 这是验证部分
        try:
            user_obj = await crud.simple_authentication(session, cl.ws.cookies, room_id)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Authentication failed for client %s: %s",
                         cl.user.id,
                         e,
                         exc_info=True)
            return
        try:
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

            await room_cache.update_room_player_online_status(room_id, cl.user.id, True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error updating player status for non-spectator: %s",
                         e,
                         exc_info=True)

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

    # 连接时发送当前播放状态
    message = RoomSchema.ClientRoomState.model_validate(room)

    # 设置回合状态
    round_state = room.round_state or 0
    if round_state not in (0, 1, 2, 3, 4):
        round_state = 0
    message.round_state = round_state

    playback_state = await room_cache.get_room_playback_state(room_id)
    if playback_state:
        message.playback_status = RoomSchema.PlaybackState.model_validate(
            playback_state)
    queue: list[AnswerQueueItem] = await room_cache.get_answer_queue(room_id)
    message.answer_queue = queue

    room_state_message = RoomSchema.RoomStateMessage(data=message)

    # 只有非观战者用户才广播加入消息
    if not is_spectator:
        try:
            player_item = cache.schemas.RoomStatePlayerItem.model_validate(cl.user)
            join_message = RoomSchema.PlayerJoinMessage(
                data=RoomSchema.RoomStatePlayerItem.model_validate(player_item))

            async def send_if_connected():
                if cl.ws.client_state == WebSocketState.CONNECTED:
                    await cl.ws.send_json(room_state_message.model_dump())
                else:
                    logger.warning("Client %s WebSocket not connected, skipping send",
                                   cl.user.id)

            res = await asyncio.gather(
                *[
                    send_if_connected(),
                    clients.broadcast(room_id,
                                      join_message.model_dump(),
                                      excluded_clients={cl}),
                ],
                return_exceptions=True,
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error sending messages for non-spectator: %s",
                         e,
                         exc_info=True)

            # 即使出错也要发送房间状态给客户端
            async def send_if_connected2():
                if cl.ws.client_state == WebSocketState.CONNECTED:
                    await cl.ws.send_json(room_state_message.model_dump())
                else:
                    logger.warning("Client %s WebSocket not connected, skipping send",
                                   cl.user.id)

            res = await asyncio.gather(
                send_if_connected2(),
                return_exceptions=True,
            )
    else:
        # 观战者用户只发送房间状态，不广播加入消息
        async def send_if_connected3():
            if cl.ws.client_state == WebSocketState.CONNECTED:
                await cl.ws.send_json(room_state_message.model_dump())
            else:
                logger.warning("Client %s WebSocket not connected, skipping send",
                               cl.user.id)

        res = await asyncio.gather(
            send_if_connected3(),
            return_exceptions=True,
        )

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

    # 检查是否是观战者用户（id为0）
    is_spectator = cl.user.id == 0

    # 只有非观战者用户才更新状态和广播离开消息
    if not is_spectator:
        try:
            player_item = cache.schemas.RoomStatePlayerItem.model_validate(cl.user)
            player_item.online = False

            leave_message = RoomSchema.PlayerLeaveMessage(
                data=RoomSchema.RoomStatePlayerItem.model_validate(player_item))
            await clients.broadcast(room_id,
                                    leave_message.model_dump(),
                                    excluded_clients={cl})

            user = select(models.User).where(models.User.id == cl.user.id)
            result = await session.execute(user)
            user_obj = result.scalar_one_or_none()
            if user_obj:
                user_obj.online = False
            await room_cache.set_room_player(room_id, player_item)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error updating player status on disconnect: %s",
                         e,
                         exc_info=True)

    # 无论是否是观战者，都从客户端管理器中移除
    clients.pop(room_id, cl)


@regist(GameEventType.START_POS_UPDATE, data_validator=StartPosUpdateData)
async def handle_start_pos_update(
    data: StartPosUpdateData,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **_kwargs,
) -> None:
    """处理起始位置更新事件

    Args:
        data (StartPosUpdateData): 起始位置数据
        clients (ClientManager): 客户端管理器
        client (Client): 当前客户端
        room_id (str): 房间 ID
    """
    # 检查是否是房主
    if not client.user.is_owner:
        logger.warning("Non-owner %s tried to update start position", client.user.id)
        return

    try:
        success = await RoomStateManager.set_start_position(room_id,
                                                            data.start_position_percent)
        if success:
            message = RoomSchema.StartPosUpdateMessage(data=data)
            await clients.broadcast(room_id, message.model_dump())
            logger.debug(
                "Start position updated to %s%% for room %s",
                data.start_position_percent,
                room_id,
            )
        else:
            logger.error("Failed to update start position for room %s", room_id)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error handling start position update: %s", e)


@regist(GameEventType.GAME_OVER, data_validator=GameOverData)
async def handle_game_over_manual(
    data: GameOverData,
    clients: ClientManager,
    client: Client,
    room_id: str,
    session: AsyncSession,
    **_kwargs,
) -> None:
    """处理游戏结束事件（手动触发）

    Args:
        data (GameOverData): 游戏结束数据
        clients (ClientManager): 客户端管理器
        client (Client): 当前客户端
        room_id (str): 房间 ID
        session (AsyncSession): 数据库会话
    """
    # 检查是否是房主
    if not client.user.is_owner:
        logger.warning("Non-owner %s tried to end game", client.user.id)
        return

    try:
        result = await RoomStateManager.end_game(room_id, session)
        if result["success"]:
            game_over_data = RoomSchema.GameOverData(
                manual=data.manual, final_scores=result["final_scores"])
            message = RoomSchema.GameOverMessage(data=game_over_data)
            await clients.broadcast(room_id, message.model_dump())
            logger.info("Game ended manually for room %s", room_id)
        else:
            logger.error("Failed to end game for room %s: %s", room_id,
                         result.get("error"))
    except WebSocketDisconnect as e:  # pylint: disable=broad-exception-caught
        logger.error("Error handling game over: %s", e)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Unexpected error handling game over: %s", e)


@regist(GameEventType.CLEAR_ANSWER_QUEUE, data_validator=ClearAnswerQueueData)
async def handle_clear_answer_queue(
    data: ClearAnswerQueueData,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **_kwargs,
) -> None:
    """处理清空抢答队列事件

    Args:
        data (ClearAnswerQueueData): 清空队列数据
        clients (ClientManager): 客户端管理器
        client (Client): 当前客户端
        room_id (str): 房间 ID
    """
    # 检查是否是房主
    if not client.user.is_owner:
        logger.warning("Non-owner %s tried to clear answer queue", client.user.id)
        return

    try:
        # 清空抢答队列
        success = await RoomStateManager.clear_answer_queue(room_id)
        if success:
            # 广播清空队列消息给所有客户端
            message = RoomSchema.ClearAnswerQueueMessage(data=data)
            await clients.broadcast(room_id, message.model_dump())
            logger.debug("Answer queue cleared for room %s", room_id)
        else:
            logger.error("Failed to clear answer queue for room %s", room_id)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error handling clear answer queue: %s", e)
