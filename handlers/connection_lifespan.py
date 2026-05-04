"""WebSocket connection lifespan event handlers."""

from __future__ import annotations

import asyncio

from fastapi import WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.websockets import WebSocketState

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
    KickUserMessage,
    StartPosUpdateData,
)
from schemas.ws_messages.judge_schemas import ShowSongData, ShowSongMessage
from db.session import session_scope
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

    所有连接（含断线重连）均广播加入消息。
    新玩家加入房间需通过 RESTful API (POST /api/room/{roomid})，
    该 API 已实现对局开始后禁止新玩家加入的逻辑。

    Args:
        session (AsyncSession): The async database session for executing queries and commits
        cl (Client): The client object representing the connected user
        clients (ClientManager): The client manager for broadcasting messages to multiple clients
        room_id (str): The ID of the room the client is connecting to

    Returns:
        None

    Raises:
        Logs a warning if the room is not found in the database during connection
    """
    # pylint: disable=too-many-locals

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

    # 更新玩家在线状态
    try:
        # 先设置online为True，避免None值在验证时出错
        cl.user.online = True

        player_item = cache.schemas.RoomStatePlayerItem.model_validate(cl.user)
        player_item.online = True

        # 确保数据库中的在线状态在当前 session 内被持久化
        user_stmt = select(models.User).where(models.User.id == cl.user.id)
        user_result = await session.execute(user_stmt)
        user_obj = user_result.scalar_one_or_none()
        if user_obj:
            user_obj.online = True

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error updating player status: %s", e, exc_info=True)

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
    message.answer_queue_tail_player_id = await room_cache.get_answer_queue_tail_player_id(
        room_id)
    message.answer_deadline = await room_cache.get_answer_deadline(room_id)

    try:
        song_id, song_index = await crud.get_current_song_info(session, room_id)
        if song_id is not None and song_index is not None:
            round_answers = await crud.get_round_answers_for_room_state(
                session=session,
                room_id=room_id,
                song_id=song_id,
                round_index=song_index,
            )
            message.round_answers = [
                RoomSchema.RoundAnswerItem.model_validate(item)
                for item in round_answers
            ]

            score_stmt = (select(models.Score.id).where(
                models.Score.room_id == room_id,
                models.Score.round_index == song_index,
            ).limit(1))
            score_result = await session.execute(score_stmt)
            message.round_scored = score_result.scalar_one_or_none() is not None
        else:
            message.round_answers = []
            message.round_scored = False
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Failed to build round_scored/round_answers in ROOM_STATE for room %s: %s",
            room_id,
            e,
        )
        message.round_answers = []
        message.round_scored = False

    room_state_message = RoomSchema.RoomStateMessage(data=message)

    reconnect_song_message: ShowSongMessage | None = None
    if room.show_answer:
        try:
            song_id, _ = await crud.get_current_song_info(session, room_id)
            if song_id is not None:
                song_stmt = select(models.Song).where(models.Song.id == song_id)
                song_result = await session.execute(song_stmt)
                song = song_result.scalar_one_or_none()
                if song is not None:
                    reconnect_song_message = ShowSongMessage(data=ShowSongData(
                        title=song.title,
                        album=song.album_name,
                        author=song.artist,
                        cover=song.cover_url,
                    ))
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Failed to build reconnect SHOW_SONG message for room %s: %s",
                room_id,
                e,
            )

    # 发送房间状态给客户端
    async def send_if_connected():
        if cl.ws.client_state == WebSocketState.CONNECTED:
            await cl.ws.send_json(room_state_message.model_dump())
            if reconnect_song_message is not None:
                await cl.ws.send_json(reconnect_song_message.model_dump())
        else:
            logger.warning("Client %s WebSocket not connected, skipping send",
                           cl.user.id)

    # 先向新连接的客户端发送房间状态，如果发送失败则不广播加入消息
    # 避免 WS 已断开时仍广播 PlayerJoinMessage 导致其他客户端收到无意义的加入/离开消息序列
    try:
        await send_if_connected()
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(
            "Failed to send initial state to client %s: %s",
            cl.user.id,
            e,
            exc_info=True,
        )
        return

    try:
        player_item = cache.schemas.RoomStatePlayerItem.model_validate(cl.user)
        join_message = RoomSchema.PlayerJoinMessage(
            data=RoomSchema.RoomStatePlayerItem.model_validate(player_item))
        await clients.broadcast(room_id,
                                join_message.model_dump(),
                                excluded_clients={cl})
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error broadcasting join message: %s", e, exc_info=True)

    logger.debug("Finished sending initial room state to client %s", cl)


async def on_disconnect(
    session: AsyncSession,
    cl: Client,
    clients: ClientManager,
    room_id: str,
) -> None:
    """
    Handle client disconnection from a room.

    对局未开始：用户离开时仅更新为离线（不自动清理玩家）
    对局开始后：同样保留玩家信息并显示断联状态，支持断线重连
    使用 try/finally 确保 clients.pop 一定执行

    Args:
        session: AsyncSession for database operations.
        cl: Client object representing the disconnected client.
        clients: ClientManager instance managing all connected connected clients.
        room_id: Unique identifier of the room the client is leaving.

    Returns:
        None
    """
    try:
        await cl.ws.close(code=1000, reason="Client disconnected")
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to close websocket for client %s", cl)

    try:
        # 获取房间状态以判断对局是否开始
        stmt = select(models.Room).where(models.Room.id == room_id)
        result = await session.execute(stmt)
        room = result.scalar_one_or_none()

        if room is None:
            logger.warning("Room %s not found during on_disconnect for client %s",
                           room_id, cl)
            return

        # 若同一用户在该房间仍有其他活跃连接（例如刷新重连后的旧连接断开），
        # 则不应将其标记为离线，也不应广播 PLAYER_LEAVE。
        # NOTE: 排除 cl 自身——cl 尚未从 clients 中 pop（在 finally 中执行），
        # 不排除自身会导致 any() 恒为 True，跳过所有清理逻辑。
        has_other_active_session = any(other.user.id == cl.user.id and other is not cl
                                       for other in clients.get_clients(room_id))
        if has_other_active_session:
            logger.info(
                "Skip offline mark for user %s in room %s because another active session exists",
                cl.user.id,
                room_id,
            )
            return

        # 判断对局是否已开始
        # room.status: 0=waiting, 1=playing, 2=ended
        # room.round_state: 0=PENDING, 1=PLAYING_AUDIO, 2=ANSWERING, 3=JUDGING, 4=COMPLETED
        is_game_started = room.status in (1, 2) or (room.round_state or 0) > 0

        # 从当前 session 加载 user_obj（避免跨 session 对象引用问题）
        user_obj = await crud.get_player_by_id(session, cl.user.id)

        # 用户已被删除（如被房主踢出）时，不再广播离开消息
        if user_obj is None:
            logger.info(
                "Player %s not found during disconnect in room %s, likely removed",
                cl.user.id,
                room_id,
            )
            return

        # 构建广播用的 player_item（在修改状态前先取快照，online 固定为 False）
        player_item = RoomSchema.RoomStatePlayerItem(
            id=cl.user.id,
            username=cl.user.username,
            is_owner=cl.user.is_owner,
            online=False,
        )

        if not is_game_started:
            logger.info(
                "Player %s left room %s before game started, keeping player and marking offline",
                cl.user.id,
                room_id,
            )
        else:
            logger.info(
                "Player %s disconnected from room %s during game, marking as offline",
                cl.user.id,
                room_id,
            )

        cl.user.online = False
        if user_obj is not None:
            user_obj.online = False

        # 广播玩家离开/离线消息
        leave_message = RoomSchema.PlayerLeaveMessage(data=player_item)
        await clients.broadcast(room_id,
                                leave_message.model_dump(),
                                excluded_clients={cl})

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error handling disconnect for client %s: %s",
                     cl,
                     e,
                     exc_info=True)
    finally:
        # 无论如何都要从客户端管理器中移除（异常保护）
        clients.pop(room_id, cl)

        # Stop audio push if room is now empty
        if clients.is_empty(room_id):
            from handlers.audio_push_task import audio_push_manager  # pylint: disable=import-outside-toplevel
            await audio_push_manager.stop_push(room_id)


@regist(GameEventType.KICK_USER, data_validator=KickUserMessage)
async def handle_kick_user(
    data: KickUserMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **_kwargs,
) -> None:
    """处理踢人事件：广播踢人消息、删除用户并关闭其连接。"""
    if not client.user.is_owner:
        logger.warning("Non-owner %s tried to kick user", client.user.id)
        await client.send_error(GameEventType.KICK_USER,
                                "Only room owner can kick users")
        return

    target_user_id = data.data.user_id
    if target_user_id == client.user.id:
        await client.send_error(GameEventType.KICK_USER, "Room owner cannot kick self")
        return

    removed_player_item: RoomSchema.RoomStatePlayerItem | None = None

    async with session_scope() as session:
        target_user = await crud.get_player_by_id(session, target_user_id)
        if target_user is None or target_user.room_id != room_id:
            await client.send_error(GameEventType.KICK_USER,
                                    f"User {target_user_id} not found in room")
            return

        removed_player_item = RoomSchema.RoomStatePlayerItem(
            id=target_user.id,
            username=target_user.username,
            is_owner=target_user.is_owner,
            online=False,
        )

        # 清理房间抢答相关状态，避免脏数据残留
        await room_cache.remove_from_answer_queue(room_id, target_user_id)
        current_answerer = await room_cache.get_room_current_answerer(room_id)
        if current_answerer == target_user_id:
            await room_cache.clear_room_current_answerer(room_id)

        # 级联删除用户（scores/player_answers 等通过 FK CASCADE 删除）
        await session.delete(target_user)

    kick_message = RoomSchema.KickUserMessage(data=RoomSchema.KickUserData(
        user_id=target_user_id))
    await clients.broadcast(room_id, kick_message.model_dump())

    if removed_player_item is not None:
        leave_message = RoomSchema.PlayerLeaveMessage(data=removed_player_item)
        await clients.broadcast(room_id, leave_message.model_dump())

    target_clients = [
        cl for cl in clients.get_clients(room_id) if cl.user.id == target_user_id
    ]
    if target_clients:
        tasks = [
            clients.kick(room_id, cl, code=4001, reason="Kicked by room owner")
            for cl in target_clients
        ]
        await asyncio.gather(*tasks, return_exceptions=True)


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
