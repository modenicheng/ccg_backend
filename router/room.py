"""Room-related API endpoints for CCG backend."""

from __future__ import annotations
import asyncio
import os
import secrets
import string
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Depends, Request, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from db.models import Room, User, TagGroup, RoomStatusORM, Song, RoomSong
from db.session import get_db
from cache.connection import get_redis
from schemas.room import JoinRoomRequest, JoinRoomResponse
from mq import tasks
from schemas.ws_messages import room_schemas as WsRoomSchema
from schemas.user import UserLogin, BaseUser
from schemas.tag import TagGroupResponse
from schemas import (
    CreateRoomResponse,
    PatchRoomRequest,
    RoomInfoResponse,
    CreateRoomRequest,
)
from utils import get_logger

logger = get_logger(__name__)

room_router = APIRouter(prefix="/api/room", tags=["room"])


def _to_room_info_response(room: Room) -> RoomInfoResponse:
    host_user = next((user for user in room.users if user.is_owner), None)
    return RoomInfoResponse(
        room_id=room.id,
        host_player_id=str(host_user.id) if host_user else "",
        status=int(room.status),
        title=room.title,
        players=[BaseUser.model_validate(user) for user in room.users],
        tag_groups=[
            TagGroupResponse.model_validate(group) for group in room.tag_groups
        ],
    )


def generate_room_id(length: int = 6) -> str:
    """Generate a random room ID."""
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


async def auto_setup_after_commit(room_id: str) -> None:
    """在房间创建后异步执行 auto-setup-test-audio"""
    await asyncio.sleep(2)  # 等待 2 秒，确保 WebSocket 已连接和房间初始化完成
    try:
        logger.info("Executing auto-setup-test-audio for room %s", room_id)
        # 直接调用 auto_setup_test_audio 函数
        from unittest.mock import Mock
        from fastapi import Request

        # 延迟导入 app，避免循环导入
        import main
        mock_request = Mock()
        mock_request.app = main.app

        # 使用 session_scope 创建新的 session
        from db.session import session_scope
        async with session_scope() as new_session:
            result = await auto_setup_test_audio(
                roomid=room_id,
                request=mock_request,
                session=new_session,
            )
            logger.info(
                "Auto-setup-test-audio completed for room %s, result: %s",
                room_id,
                result,
            )
    except Exception as e:
        logger.error("Auto-setup-test-audio failed for room %s: %s",
                     room_id,
                     e,
                     exc_info=True)


@room_router.post("/", response_model=CreateRoomResponse)
async def create_room(
    info: CreateRoomRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db)
) -> CreateRoomResponse:
    """Create a new room."""
    room_id = generate_room_id()
    logger.info("Creating room with ID: %s", room_id)

    owner = User(username=info.host_name,
                 is_owner=True,
                 token=str(uuid4()),
                 room_id=room_id)
    session.add(owner)

    new_room = Room(id=room_id, title=info.title, users=[owner])
    session.add(new_room)

    try:
        await get_redis()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc
    await session.commit()

    # 房间创建成功后，异步触发 auto-setup-test-audio（不阻塞响应）
    logger.info(
        "Room created successfully, scheduling auto-setup-test-audio for room %s",
        room_id)

    # 使用 BackgroundTasks 确保任务会执行
    background_tasks.add_task(
        auto_setup_after_commit,
        room_id,
    )
    logger.info("Background task scheduled for room %s", room_id)

    return CreateRoomResponse(
        room_id=room_id,
        host=UserLogin.model_validate(owner),
    )


@room_router.post("/{roomid}", response_model=JoinRoomResponse)
async def join_room(
    roomid: str, data: JoinRoomRequest,
    session: AsyncSession = Depends(get_db)) -> JoinRoomResponse:
    """Join an existing room."""
    stmt = select(Room).where(Room.id == roomid)
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # 如果房间已经在游戏中，拒绝加入
    if room.status != RoomStatusORM.WAITING:
        raise HTTPException(status_code=400,
                            detail="Cannot join a room that is not in waiting state")

    new_user = User(username=data.username,
                    is_owner=False,
                    token=str(uuid4()),
                    room=room)
    try:
        session.add(new_user)
        await session.commit()
    except IntegrityError as exc:
        raise HTTPException(status_code=400,
                            detail="Username already taken in this room") from exc
    return JoinRoomResponse(
        room_id=roomid,
        user=UserLogin.model_validate(new_user),
    )


@room_router.get("/{roomid}", response_model=RoomInfoResponse)
async def room_info(
    roomid: str, session: AsyncSession = Depends(get_db)) -> RoomInfoResponse:
    """Get room information."""
    stmt = (select(Room).where(Room.id == roomid).options(
        selectinload(Room.users),
        selectinload(Room.tag_groups).selectinload(TagGroup.tags),
    ))
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return _to_room_info_response(room)


@room_router.patch("/{roomid}", response_model=RoomInfoResponse)
async def room_setting(
    roomid: str,
    payload: PatchRoomRequest,
    request: Request,
    session: AsyncSession = Depends(get_db)) -> RoomInfoResponse:
    """Update room settings."""
    stmt = (select(Room).where(Room.id == roomid).options(
        selectinload(Room.users),
        selectinload(Room.tag_groups).selectinload(TagGroup.tags),
    ))
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if payload.song_queue is not None:
        # await room_cache.set_room_song_queue(roomid, payload.song_queue)
        # 先直接存数据库
        pass

    if payload.title is not None:
        room.title = payload.title

    if payload.tag_group_ids is not None or payload.tag_groups is not None:
        group_ids = payload.tag_group_ids
        if group_ids is None:
            group_ids = [group.id for group in (payload.tag_groups or [])]
        group_ids = list(dict.fromkeys(group_ids))
        if group_ids:
            group_result = await session.execute(
                select(TagGroup).where(TagGroup.id.in_(group_ids)).options(
                    selectinload(TagGroup.tags)))
            found_groups = list(group_result.scalars().all())
            found_ids = {group.id for group in found_groups}
            missing_ids = [
                group_id for group_id in group_ids if group_id not in found_ids
            ]
            if missing_ids:
                raise HTTPException(
                    status_code=404,
                    detail=f"Tag groups with IDs {missing_ids} not found",
                )
            room.tag_groups = found_groups
        else:
            room.tag_groups = []

    await session.commit()

    refreshed_result = await session.execute(stmt)
    refreshed_room = refreshed_result.scalar_one_or_none()
    if not refreshed_room:
        raise HTTPException(status_code=404, detail="Room not found")

    # 仅在房间 taggroup 选择配置变更时推送 TAG_GROUP 专用事件（不发送全量 ROOM_STATE）
    if payload.tag_group_ids is not None or payload.tag_groups is not None:
        try:
            message = WsRoomSchema.TagGroupMessage(data=WsRoomSchema.TagGroupData(
                room_id=roomid,
                tag_groups=[
                    WsRoomSchema.RoomStateTagGroupItem.model_validate(group)
                    for group in refreshed_room.tag_groups
                ],
            ))
            await request.app.state.clients.broadcast(roomid, message.model_dump())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to broadcast TAG_GROUP for room %s: %s", roomid, exc)

    return _to_room_info_response(refreshed_room)


class SetTestAudioRequest(BaseModel):
    song_id: int = Field(..., gt=0, description="歌曲数据库 ID")


@room_router.post("/{roomid}/set-test-audio")
async def set_test_audio(
        roomid: str,
        payload: SetTestAudioRequest,
        request: Request,
        session: AsyncSession = Depends(get_db),
):
    """手动设置房间 test_audio (从全局歌曲库选择)

    逻辑：
    1. 验证歌曲是否存在于全局歌曲库
    2. 更新房间的 test_audio_song_id
    3. 触发预下载 (如果需要)
    4. 广播 PLAY 事件 (使用正常播放链路)
    5. 持久化播放状态到 Redis
    """
    # 获取房间
    room_stmt = select(Room).where(Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # 验证歌曲是否存在于全局歌曲库
    song_stmt = select(Song).where(Song.id == payload.song_id)
    song_result = await session.execute(song_stmt)
    song = song_result.scalar_one_or_none()

    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    # 更新房间的 test_audio_song_id
    logger.info(
        "Setting room %s test_audio_song_id to %s (song: %s)",
        roomid,
        payload.song_id,
        song.title,
    )
    room.test_audio_song_id = payload.song_id
    await session.commit()
    await session.refresh(room)  # 刷新以确保后续代码能看到更新后的值

    # 如果歌曲未缓存，先下载
    if not song.cached_path or not os.path.exists(
            song.cached_path if song.cached_path else ""):
        try:
            if song.platform_song_id:
                logger.info(
                    "Song %s not cached, downloading before broadcast...",
                    song.id,
                )
                from mq.tasks import _download_and_cache_song_impl
                downloaded_path = await _download_and_cache_song_impl(
                    song.platform_song_id)

                if downloaded_path and os.path.exists(downloaded_path):
                    logger.info(
                        "Successfully downloaded song: %s (%s)",
                        song.id,
                        downloaded_path,
                    )
                    # 重新加载歌曲记录以获取最新的 cached_path
                    await session.refresh(song)
                else:
                    logger.error(
                        "Failed to download song %s, downloaded_path: %s",
                        song.id,
                        downloaded_path,
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to download audio file for song {song.id}",
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                "Failed to download song %s: %s",
                song.id,
                e,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Exception occurred while downloading song: {str(e)}",
            ) from e

    # 广播 PLAY 事件 (使用正常播放链路)
    audio_url = f"/api/songs/file/{payload.song_id}"
    current_ts = int(__import__('time').time() * 1000)

    try:
        logger.info(
            "Starting to broadcast PLAY for room %s, song_id: %s, audio_url: %s",
            roomid,
            payload.song_id,
            audio_url,
        )
        play_message = {
            "event": 20,  # GameEventType.PLAY.value
            "ts": current_ts,
            "data": {
                "audio_url": audio_url,
                "progress_ms": 0,
                "offset_ts": current_ts,
            },
        }
        await request.app.state.clients.broadcast(roomid, play_message)
        logger.info(
            "Broadcasted PLAY for test audio: %s (song_id: %s)",
            audio_url,
            payload.song_id,
        )

        # 持久化播放状态到 Redis
        from cache.room_cache import set_room_playback_state
        from cache.schemas import PlaybackState

        playback_state = PlaybackState(
            audio_url=audio_url,
            progress_ms=0,
            updated_at=current_ts,
            offset_ts=current_ts,
            play_state="playing",
            current_order=-1,  # -1 表示 test_audio
        )
        await set_room_playback_state(roomid, playback_state)
        logger.info(
            "Persisted test audio playback state to Redis: %s",
            roomid,
        )
    except Exception as exc:
        logger.error(
            "Failed to broadcast or persist test audio state for room %s: %s",
            roomid,
            exc,
        )
        raise HTTPException(status_code=500, detail=f"Failed to broadcast: {str(exc)}")

    return {
        "success": True,
        "song_id": payload.song_id,
        "title": song.title,
        "message": "Test audio set successfully",
    }


@room_router.post("/{roomid}/auto-setup-test-audio")
async def auto_setup_test_audio(
        roomid: str,
        request: Request,
        session: AsyncSession = Depends(get_db),
):
    """自动设置房间 test_audio (使用 CDN 播放默认 BGM)

    逻辑：
    1. 检查房间是否已经设置了 test_audio_song_id
    2. 如果未设置，设置为 -1 (表示使用默认 CDN BGM)
    3. 广播 CDN URL 的 PLAY 事件
    """
    # 获取房间
    room_stmt = select(Room).where(Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # 检查是否已经设置了 test_audio_song_id
    if room.test_audio_song_id is not None:
        logger.info(
            "Room %s already has test_audio_song_id: %s",
            roomid,
            room.test_audio_song_id,
        )
        # 使用自定义歌曲
        return await _broadcast_test_audio_from_db(roomid, request, session, room.test_audio_song_id)

    # 未设置 test_audio_song_id (NULL)，使用默认 CDN BGM
    logger.info(
        "Room %s test_audio_song_id is NULL, using default CDN BGM",
        roomid,
    )

    # 广播 CDN URL 的 PLAY 事件
    return await _broadcast_test_audio_from_cdn(roomid, request, session)


async def _broadcast_test_audio_from_cdn(
    roomid: str,
    request: Request,
    session: AsyncSession,
):
    """从 CDN 广播 test_audio (默认 BGM)"""
    # 默认 CDN BGM URL
    cdn_audio_url = "https://cdn.modenc.top/files/001gQVVQ0WD3Al.ogg"
    current_ts = int(__import__('time').time() * 1000)

    # 检查是否有客户端连接
    clients_manager = request.app.state.clients
    has_clients = clients_manager.room_size(roomid) > 0

    if has_clients:
        try:
            logger.info(
                "Broadcasting test audio from CDN for room %s: %s (clients: %d)",
                roomid,
                cdn_audio_url,
                clients_manager.room_size(roomid),
            )
            # 广播 PLAY 事件
            play_message = {
                "event": 20,  # GameEventType.PLAY.value
                "ts": current_ts,
                "data": {
                    "audio_url": cdn_audio_url,
                    "progress_ms": 0,
                    "offset_ts": current_ts,
                },
            }
            await clients_manager.broadcast(roomid, play_message)
            logger.info(
                "Broadcasted PLAY for test audio from CDN: %s",
                cdn_audio_url,
            )

            # 持久化播放状态到 Redis
            from cache.room_cache import set_room_playback_state
            from cache.schemas import PlaybackState

            playback_state = PlaybackState(
                audio_url=cdn_audio_url,
                progress_ms=0,
                updated_at=current_ts,
                offset_ts=current_ts,
                play_state="playing",
                current_order=-1,  # -1 表示 test_audio
            )
            await set_room_playback_state(roomid, playback_state)
            logger.info(
                "Persisted test audio playback state to Redis: %s",
                roomid,
            )
        except Exception as exc:
            logger.error(
                "Failed to broadcast or persist test audio state for room %s: %s",
                roomid,
                exc,
            )
            raise HTTPException(status_code=500, detail=f"Failed to broadcast: {str(exc)}")
    else:
        logger.info(
            "Skipping broadcast for room %s: no clients connected yet. test_audio_song_id is set to -1.",
            roomid,
        )
        # 即使没有客户端，也要持久化播放状态到 Redis
        try:
            from cache.room_cache import set_room_playback_state
            from cache.schemas import PlaybackState

            playback_state = PlaybackState(
                audio_url=cdn_audio_url,
                progress_ms=0,
                updated_at=current_ts,
                offset_ts=current_ts,
                play_state="paused",  # 没有客户端时设置为 paused
                current_order=-1,  # -1 表示 test_audio
            )
            await set_room_playback_state(roomid, playback_state)
            logger.info(
                "Persisted test audio playback state to Redis (paused): %s",
                roomid,
            )
        except Exception as exc:
            logger.error(
                "Failed to persist test audio state to Redis for room %s: %s",
                roomid,
                exc,
            )

    return {
        "success": True,
        "song_id": -1,  # -1 表示使用 CDN
        "platform_song_id": "001gQVVQ0WD3Al",
        "message": "Test audio set to default CDN BGM",
    }


async def _broadcast_test_audio_from_db(
    roomid: str,
    request: Request,
    session: AsyncSession,
    song_id: int,
):
    """从数据库广播 test_audio (自定义歌曲)"""
    # 获取歌曲
    song_stmt = select(Song).where(Song.id == song_id)
    song_result = await session.execute(song_stmt)
    song = song_result.scalar_one_or_none()

    if not song:
        raise HTTPException(status_code=404, detail=f"Song {song_id} not found")

    # 如果歌曲未缓存，触发下载
    if not song.cached_path or not os.path.exists(song.cached_path):
        try:
            from mq.tasks import _download_and_cache_song_impl
            assert song.platform_song_id is not None, "platform_song_id cannot be None"
            logger.info(
                "Downloading and caching song %s for test audio",
                song.platform_song_id,
            )
            cached_path = await _download_and_cache_song_impl(song.platform_song_id)
            if cached_path:
                logger.info(
                    "Successfully cached song %s at %s",
                    song.platform_song_id,
                    cached_path,
                )
                await session.refresh(song)
            else:
                logger.warning(
                    "Failed to cache song %s, proceeding without cache",
                    song.platform_song_id,
                )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Failed to download and cache song %s: %s",
                song.id,
                e,
            )

    # 构建音频 URL
    audio_url = f"/api/songs/file/{song.id}"
    current_ts = int(__import__('time').time() * 1000)

    # 检查是否有客户端连接
    clients_manager = request.app.state.clients
    has_clients = clients_manager.room_size(roomid) > 0

    if has_clients:
        try:
            logger.info(
                "Broadcasting test audio from DB for room %s: %s (song_id: %s, clients: %d)",
                roomid,
                audio_url,
                song.id,
                clients_manager.room_size(roomid),
            )
            # 广播 PLAY 事件
            play_message = {
                "event": 20,  # GameEventType.PLAY.value
                "ts": current_ts,
                "data": {
                    "audio_url": audio_url,
                    "progress_ms": 0,
                    "offset_ts": current_ts,
                },
            }
            await clients_manager.broadcast(roomid, play_message)
            logger.info(
                "Broadcasted PLAY for test audio from DB: %s (song_id: %s)",
                audio_url,
                song.id,
            )

            # 持久化播放状态到 Redis
            from cache.room_cache import set_room_playback_state
            from cache.schemas import PlaybackState

            playback_state = PlaybackState(
                audio_url=audio_url,
                progress_ms=0,
                updated_at=current_ts,
                offset_ts=current_ts,
                play_state="playing",
                current_order=-1,  # -1 表示 test_audio
            )
            await set_room_playback_state(roomid, playback_state)
            logger.info(
                "Persisted test audio playback state to Redis: %s",
                roomid,
            )
        except Exception as exc:
            logger.error(
                "Failed to broadcast or persist test audio state for room %s: %s",
                roomid,
                exc,
            )
            raise HTTPException(status_code=500, detail=f"Failed to broadcast: {str(exc)}")
    else:
        logger.info(
            "Skipping broadcast for room %s: no clients connected yet. test_audio_song_id is set to %s.",
            roomid,
            song_id,
        )
        # 即使没有客户端，也要持久化播放状态到 Redis
        try:
            from cache.room_cache import set_room_playback_state
            from cache.schemas import PlaybackState

            playback_state = PlaybackState(
                audio_url=audio_url,
                progress_ms=0,
                updated_at=current_ts,
                offset_ts=current_ts,
                play_state="paused",  # 没有客户端时设置为 paused
                current_order=-1,  # -1 表示 test_audio
            )
            await set_room_playback_state(roomid, playback_state)
            logger.info(
                "Persisted test audio playback state to Redis (paused): %s",
                roomid,
            )
        except Exception as exc:
            logger.error(
                "Failed to persist test audio state to Redis for room %s: %s",
                roomid,
                exc,
            )

    return {
        "success": True,
        "song_id": song.id,
        "platform_song_id": song.platform_song_id,
        "title": song.title,
        "message": "Test audio set from database",
    }


@room_router.delete("/{roomid}/dissolve")
async def dissolve_room(
        roomid: str,
        request: Request,
        session: AsyncSession = Depends(get_db),
):
    """解散房间：记录日志并从数据库删除房间

    逻辑：
    1. 验证房间是否存在
    2. 记录房间日志（玩家信息、游戏状态等）
    3. 删除房间（级联删除相关数据）
    4. 通知所有客户端房间已解散
    """
    # 获取房间（预加载用户和标签组关系）
    from sqlalchemy.orm import selectinload
    room_stmt = (
        select(Room)
        .where(Room.id == roomid)
        .options(
            selectinload(Room.users),
            selectinload(Room.tag_groups),
        )
    )
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()

    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # 记录房间日志（在删除之前）
    player_list = [{
        "id": user.id,
        "username": user.username,
        "is_owner": user.is_owner,
    } for user in room.users]

    logger.info(
        "Dissolving room %s: title=%s, status=%s, players=%s, tag_groups=%s",
        roomid,
        room.title,
        room.status,
        player_list,
        len(room.tag_groups),
    )

    try:
        # 通知所有客户端房间已解散（使用通用错误消息）
        try:
            from schemas.ws_messages.error_schemas import WebSocketErrorEvent
            from utils.enumerations import ErrorEventType

            error_message = WebSocketErrorEvent(
                error_event=ErrorEventType.HANDLER_EXCEPTION,
                message="Room has been dissolved",
            )
            await request.app.state.clients.broadcast(roomid,
                                                      error_message.model_dump())
            logger.info("Broadcasted room dissolved message to room %s", roomid)
        except Exception as broadcast_error:
            logger.error("Failed to broadcast room dissolved message: %s",
                         broadcast_error)

        # 删除房间（级联删除相关数据）
        await session.delete(room)
        await session.commit()

        # 清理 Redis 缓存（防止房间被删除后仍可连接）
        try:
            from cache.room_cache import delete_all_room_cache
            
            # 删除所有房间缓存
            await delete_all_room_cache(roomid)
            
            logger.info("Cleared Redis cache for dissolved room %s", roomid)
        except Exception as cache_error:
            logger.warning("Failed to clear Redis cache for room %s: %s", roomid, cache_error)

        logger.info("Room %s dissolved successfully", roomid)

        return {
            "success": True,
            "message": "Room dissolved successfully",
            "room_id": roomid,
        }
    except Exception as e:
        logger.error("Failed to dissolve room %s: %s", roomid, e, exc_info=True)
        await session.rollback()
        raise HTTPException(status_code=500,
                            detail=f"Failed to dissolve room: {str(e)}") from e
