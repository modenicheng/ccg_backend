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
        logger.error("Auto-setup-test-audio failed for room %s: %s", room_id, e, exc_info=True)


@room_router.post("/", response_model=CreateRoomResponse)
async def create_room(
    info: CreateRoomRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db)) -> CreateRoomResponse:
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
    logger.info("Room created successfully, scheduling auto-setup-test-audio for room %s", room_id)
    
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
    """手动设置房间 test_audio，触发预下载和播放
    
    逻辑：
    1. 验证歌曲是否在房间歌单中
    2. 广播 PRELOAD_AUDIO 事件
    3. 广播 PLAY 事件
    4. 持久化播放状态到 Redis
    """
    # 获取房间
    room_stmt = select(Room).where(Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    
    # 验证歌曲是否在房间歌单中
    room_song_stmt = select(RoomSong).where(
        RoomSong.room_id == roomid,
        RoomSong.song_id == payload.song_id,
    )
    room_song_result = await session.execute(room_song_stmt)
    room_song = room_song_result.scalar_one_or_none()
    
    if not room_song:
        raise HTTPException(
            status_code=400,
            detail="Song not in room playlist, please add song to room first",
        )
    
    # 获取歌曲信息
    song_stmt = select(Song).where(Song.id == payload.song_id)
    song_result = await session.execute(song_stmt)
    song = song_result.scalar_one_or_none()
    
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    
    # 如果歌曲未缓存，先下载
    if not song.cached_path or not os.path.exists(song.cached_path if song.cached_path else ""):
        try:
            if song.platform_song_id:
                logger.info(
                    "Song %s not cached, downloading before broadcast...",
                    song.id,
                )
                from mq.tasks import _download_and_cache_song_impl
                downloaded_path = await _download_and_cache_song_impl(song.platform_song_id)
                
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
    
    # 广播 PRELOAD_AUDIO 和 PLAY 事件
    audio_url = f"/api/songs/file/{payload.song_id}"
    current_ts = int(__import__('time').time() * 1000)
    
    try:
        logger.info(
            "Starting to broadcast PRELOAD_AUDIO for room %s, song_id: %s, audio_url: %s",
            roomid,
            payload.song_id,
            audio_url,
        )
        # 广播 PRELOAD_AUDIO 事件
        preload_message = {
            "event": 23,  # GameEventType.PRELOAD_AUDIO.value
            "ts": current_ts,
            "data": {
                "audio_url": audio_url,
                "progress_ms": 0,
                "offset_ts": None,
            },
        }
        await request.app.state.clients.broadcast(roomid, preload_message)
        logger.info(
            "Broadcasted PRELOAD_AUDIO for test audio: %s (song_id: %s)",
            audio_url,
            payload.song_id,
        )
        
        # 等待短暂延迟确保预下载开始（至少 3 秒，确保音频文件已下载完成）
        logger.info("Waiting 3000ms before broadcasting PLAY event (waiting for preload)...")
        await asyncio.sleep(3.0)
        
        # 广播 PLAY 事件
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
            current_order=0,
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
    """自动设置房间 test_audio
    
    逻辑：
    1. 检查默认歌曲（platform_song_id: "001gQVVQ0WD3Al"）是否在房间歌单中
    2. 如果不在，检查是否在数据库总歌曲库中
    3. 如果不在数据库中，从 QQ 音乐导入
    4. 将歌曲添加到房间歌单第一首
    5. 设置 test_audio 为该歌曲的数据库 ID
    """
    # 获取房间
    room_stmt = select(Room).where(Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # 默认 test_audio 的 platform_song_id
    default_platform_song_id = "001gQVVQ0WD3Al"

    # 1. 检查是否已在房间歌单中（一次性加载关联 song 避免懒加载引发 async 问题）
    room_song_stmt = select(RoomSong).options(
        selectinload(RoomSong.song)
    ).where(
        RoomSong.room_id == roomid,
        RoomSong.song_order == 0  # 第一首
    )
    room_song_result = await session.execute(room_song_stmt)
    first_room_song = room_song_result.scalar_one_or_none()

    if first_room_song and first_room_song.song:
        # 检查第一首歌是否是默认歌曲
        if first_room_song.song.platform_song_id == default_platform_song_id:
            logger.info(
                "Default test audio already in room playlist: %s",
                default_platform_song_id,
            )
            return {
                "success": True,
                "song_id": first_room_song.song_id,
                "platform_song_id": default_platform_song_id,
                "message": "Default test audio already set",
            }

    # 2. 检查是否在数据库总歌曲库中
    song_stmt = select(Song).where(
        Song.platform_song_id == default_platform_song_id
    )
    song_result = await session.execute(song_stmt)
    song = song_result.scalar_one_or_none()

    if not song:
        # 3. 从 QQ 音乐导入（简化版本：直接创建歌曲记录）
        logger.info(
            "Default test audio not in database, creating song record: %s",
            default_platform_song_id,
        )
        try:
            # 直接创建歌曲记录（简化版本）
            song = Song(
                platform_song_id=default_platform_song_id,
                title="默认预热 BGM",
                artist="未知",
                platform="qq",
            )
            session.add(song)
            await session.flush()  # 获取 song.id
            await session.commit()  # 立即 commit，确保歌曲记录对其他 session 可见
            await session.refresh(song)

            logger.info(
                "Created default test audio song: %s",
                default_platform_song_id,
            )
        except Exception as e:  # pylint: disable=raise-missing-from
            logger.error("Failed to create default test audio: %s", e)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create song: {str(e)}",
            ) from e

    # 如果歌已经存在，但未缓存则触发缓存任务并等待下载完成
    if song and (
        not song.cached_path
        or not os.path.exists(song.cached_path if song.cached_path else "")
    ):
        try:
            if song.platform_song_id:
                logger.info(
                    "Triggering cache task for song_id %s (%s), waiting for download...",
                    song.id,
                    song.platform_song_id,
                )
                # 同步等待下载完成（最多等待 60 秒）
                from mq.tasks import _download_and_cache_song_impl
                downloaded_path = await _download_and_cache_song_impl(song.platform_song_id)
                
                if downloaded_path and os.path.exists(downloaded_path):
                    logger.info(
                        "Successfully downloaded and cached song: %s (%s)",
                        song.id,
                        downloaded_path,
                    )
                    # 重新加载歌曲记录以获取最新的 cached_path
                    await session.refresh(song)
                else:
                    logger.error(
                        "Song download completed but file not found: %s, downloaded_path: %s",
                        song.id,
                        downloaded_path,
                    )
                    # 下载失败，返回错误
                    return {
                        "success": False,
                        "error": f"Failed to download audio file for song {song.id}",
                    }
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "Failed to download and cache song %s: %s",
                song.id,
                e,
                exc_info=True,
            )
            return {
                "success": False,
                "error": f"Exception occurred while downloading song: {str(e)}",
            }
    
    # 检查最终歌曲是否已缓存（双重检查）
    if not song or not song.cached_path or not os.path.exists(song.cached_path):
        logger.error(
            "Song %s is not cached after download attempt, cannot proceed with broadcast",
            song.id if song else "N/A",
        )
        return {
            "success": False,
            "error": "Failed to download and cache audio file",
        }

    # 4. 将歌曲添加到房间歌单第一首
    # 先检查是否已经在房间歌单中（但不是第一首）
    existing_room_song_stmt = select(RoomSong).where(
        RoomSong.room_id == roomid,
        RoomSong.song_id == song.id,
    )
    existing_room_song_result = await session.execute(existing_room_song_stmt)
    existing_room_song = existing_room_song_result.scalar_one_or_none()

    if existing_room_song:
        # 调整位置到第一首
        existing_room_song.song_order = 0
        logger.info("Moved existing song to first position: %s", song.id)
    else:
        # 添加为新歌曲
        room_song = RoomSong(
            room_id=roomid,
            song_id=song.id,
            song_order=0,
        )
        session.add(room_song)
        logger.info("Added song to room playlist: %s", song.id)

    # 调整其他歌曲的位置
    await session.execute(
        update(RoomSong).where(
            RoomSong.room_id == roomid,
            RoomSong.song_id != song.id,
        ).values(song_order=RoomSong.song_order + 1)
    )

    await session.commit()
    
    # 5. 广播 PRELOAD_AUDIO 和 PLAY 事件，触发所有客户端预下载并播放
    audio_url = f"/api/songs/file/{song.id}"
    current_ts = int(__import__('time').time() * 1000)
    
    try:
        logger.info(
            "Starting auto-setup broadcast for room %s, song_id: %s, audio_url: %s",
            roomid,
            song.id,
            audio_url,
        )
        # 广播 PRELOAD_AUDIO 事件
        preload_message = {
            "event": 23,  # GameEventType.PRELOAD_AUDIO.value
            "ts": current_ts,
            "data": {
                "audio_url": audio_url,
                "progress_ms": 0,
                "offset_ts": None,
            },
        }
        await request.app.state.clients.broadcast(roomid, preload_message)
        logger.info(
            "Broadcasted PRELOAD_AUDIO for test audio: %s (song_id: %s)",
            audio_url,
            song.id,
        )
        
        # 等待短暂延迟确保预下载开始（至少 3 秒，确保音频文件已下载完成）
        logger.info("Waiting 3000ms before broadcasting PLAY event (waiting for preload)...")
        await asyncio.sleep(3.0)
        
        # 广播 PLAY 事件
        logger.info(
            "Starting to broadcast PLAY for room %s, song_id: %s, audio_url: %s",
            roomid,
            song.id,
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
            current_order=0,
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
        # 不抛出异常，因为 auto-setup 已经完成，广播失败不影响核心功能
    
    # 6. 返回歌曲信息
    logger.info(
        "Auto-setup test audio completed: %s (DB ID: %s)",
        default_platform_song_id,
        song.id,
    )
    return {
        "success": True,
        "song_id": song.id,
        "platform_song_id": default_platform_song_id,
        "title": song.title,
        "message": "Test audio auto-setup completed",
    }
