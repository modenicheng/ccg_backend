"""Room songs management endpoints."""

from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import crud, models
from db.session import get_db
from cache import room_cache
import cache.schemas as cache_schemas
from schemas.room_songs import (
    RoomSongsListResponse,
    RoomSongResponse,
    AddRoomSongsRequest,
    RemoveRoomSongsRequest,
    BatchUpdateRoomSongOrderRequest,
)
from schemas.song import SongResponse
from mq import tasks

from utils import get_logger, get_audio_stream_url
from utils.enumerations import RoomStatus

logger = get_logger(__name__)

room_songs_router = APIRouter(prefix="/api/rooms/{roomid}/songs", tags=["room_songs"])


async def _require_room_owner(session: AsyncSession, request: Request,
                              roomid: str) -> models.User:
    """验证请求用户是当前房间房主。"""
    user = await crud.authenticate_user_for_room_http(
        session,
        roomid,
        request.query_params,
        request.cookies,
    )
    if not user:
        raise HTTPException(status_code=403,
                            detail="Authentication required for this room")
    if not user.is_owner:
        raise HTTPException(status_code=403,
                            detail="Only room owner can perform this action")
    return user


async def _require_room_waiting(session: AsyncSession, roomid: str) -> None:
    """验证房间处于 WAITING 状态，否则拒绝操作。"""
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.status != RoomStatus.WAITING.value:
        raise HTTPException(
            status_code=400,
            detail=
            "Room is not in WAITING state; song list modifications are not allowed",
        )


async def _trigger_preload_top_songs(session: AsyncSession, roomid: str) -> None:
    """在歌曲列表变更后触发前3首歌曲的预下载（仅支持 QQ 平台）。"""
    try:
        platform_song_ids = await crud.prepare_preload_songs(
            session,
            roomid,
            start_index=0,
            count=3,
        )
        triggered_count = 0
        for platform_song_id in platform_song_ids:
            try:
                tasks.download_and_cache_song(platform_song_id)
                triggered_count += 1
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Failed to enqueue preload task for room %s, platform_song_id=%s: %s",
                    roomid,
                    platform_song_id,
                    e,
                )
        logger.info(
            "Triggered preload tasks for %s songs in room %s",
            triggered_count,
            roomid,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to trigger preload for room %s: %s", roomid, e)


async def _refresh_default_playback_initial_song(session: AsyncSession,
                                                 roomid: str) -> None:
    """根据最新歌曲顺序刷新默认播放初始曲目。"""
    song_queue = await crud.get_room_song_queue(session, roomid)
    if not song_queue:
        await room_cache.delete_room_playback_state(roomid)
        logger.info(
            "Cleared playback state while refreshing default initial song "
            "for room %s (empty queue)",
            roomid,
        )
        return

    await crud.update_room_current_song_index(session, roomid, 0)
    first_song_id = song_queue[0]
    audio_token = await crud.get_or_create_audio_token(session, roomid, first_song_id)
    audio_url = get_audio_stream_url(audio_token)
    playback_state = cache_schemas.PlaybackState(
        play_state="paused",
        progress_ms=0,
        offset_ts=0,
        current_order=0,
        audio_url=audio_url,
    )
    await room_cache.set_room_playback_state(roomid, playback_state)
    logger.info(
        "Refreshed default playback initial song for room %s: song_id=%s",
        roomid,
        first_song_id,
    )


@room_songs_router.get("/", response_model=RoomSongsListResponse)
async def get_room_songs_list(
        roomid: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=1000),
        kw: str | None = Query(default=None, max_length=100),
        session: AsyncSession = Depends(get_db),
) -> RoomSongsListResponse:
    """Get all songs in a room."""
    # First check if room exists
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Get room songs with song details
    room_songs = await crud.get_room_songs(session,
                                           roomid,
                                           include_song_details=True,
                                           offset=offset,
                                           limit=limit,
                                           kw=kw)

    total = await crud.count_room_songs(session, roomid, kw=kw) or 0

    # Convert to response format
    song_responses = []
    for rs in room_songs:
        # Convert SQLAlchemy Song object to dictionary for SongResponse
        if hasattr(rs, "song") and rs.song:
            song_dict = {
                c.name: getattr(rs.song, c.name) for c in rs.song.__table__.columns
            }
            song_response = SongResponse.model_validate(song_dict)
        else:
            # If song is not loaded, create a minimal SongResponse
            song_response = SongResponse(id=rs.song_id)

        room_song_response = RoomSongResponse(
            room_id=rs.room_id,
            song_id=rs.song_id,
            song_order=rs.song_order,
            song=song_response,
        )
        song_responses.append(room_song_response)

    return RoomSongsListResponse(room_id=roomid, list=song_responses, total=total)


@room_songs_router.post("/", response_model=RoomSongsListResponse)
async def add_songs_to_room(
        roomid: str,
        request: AddRoomSongsRequest,
        http_request: Request,
        session: AsyncSession = Depends(get_db),
) -> RoomSongsListResponse:
    """Add songs to a room."""
    # Check if room exists
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    await _require_room_owner(session, http_request, roomid)
    await _require_room_waiting(session, roomid)

    # Check if songs exist
    song_stmt = select(models.Song).where(models.Song.id.in_(request.song_ids))
    song_result = await session.execute(song_stmt)
    existing_songs = list(song_result.scalars().all())
    existing_song_ids = {song.id for song in existing_songs}

    missing_song_ids = [sid for sid in request.song_ids if sid not in existing_song_ids]
    if missing_song_ids:
        raise HTTPException(status_code=404,
                            detail=f"Songs with IDs {missing_song_ids} not found")

    # Add songs to room
    try:
        added = await crud.add_songs_to_room(session,
                                             roomid,
                                             request.song_ids,
                                             append_to_end=request.append_to_end)
        await session.commit()
        if added:
            await _trigger_preload_top_songs(session, roomid)
        logger.info("Added %s songs to room %s", len(added), roomid)
    except Exception as e:
        logger.error("Failed to add songs to room %s: %s", roomid, e)
        await session.rollback()
        raise HTTPException(status_code=500,
                            detail=f"Failed to add songs to room: {str(e)}") from e  # pylint: disable=raise-missing-from

    # Return updated list
    return await get_room_songs_list(roomid, offset=0, limit=20, session=session)


@room_songs_router.delete("/", response_model=RoomSongsListResponse)
async def remove_songs_from_room(
        roomid: str,
        request: RemoveRoomSongsRequest,
        http_request: Request,
        session: AsyncSession = Depends(get_db),
) -> RoomSongsListResponse:
    """Remove songs from a room."""
    # Check if room exists
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    await _require_room_owner(session, http_request, roomid)
    await _require_room_waiting(session, roomid)

    # Remove songs
    try:
        removed_count = await crud.remove_songs_from_room(session, roomid,
                                                          request.song_ids)
        await session.commit()
        if removed_count > 0:
            await _trigger_preload_top_songs(session, roomid)
        logger.info("Removed %s songs from room %s", removed_count, roomid)
    except Exception as e:
        logger.error("Failed to remove songs from room %s: %s", roomid, e)
        await session.rollback()
        raise HTTPException(status_code=500,
                            detail=f"Failed to remove songs from room: {str(e)}") from e  # pylint: disable=raise-missing-from

    # Return updated list
    return await get_room_songs_list(roomid, offset=0, limit=20, session=session)


@room_songs_router.put("/", response_model=RoomSongsListResponse)
async def batch_update_room_song_order(
        roomid: str,
        request: BatchUpdateRoomSongOrderRequest,
        http_request: Request,
        session: AsyncSession = Depends(get_db),
) -> RoomSongsListResponse:
    """Batch update song orders in a room."""
    # Check if room exists
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    await _require_room_owner(session, http_request, roomid)
    await _require_room_waiting(session, roomid)

    # Validate all songs exist in room
    for order_update in request.orders:
        room_song = await crud.get_room_song(session, roomid, order_update.song_id)
        if not room_song:
            raise HTTPException(
                status_code=404,
                detail=f"Song with ID {order_update.song_id} not found in room",
            )

    # Apply updates (simple sequential update - for complex reordering,
    # client should send complete new ordering)
    try:
        for order_update in request.orders:
            await crud.update_room_song_order(session, roomid, order_update.song_id,
                                              order_update.new_order)
        await session.commit()
        if request.orders:
            await _trigger_preload_top_songs(session, roomid)
        logger.info(
            "Updated song orders for %s songs in room %s",
            len(request.orders),
            roomid,
        )
    except Exception as e:
        logger.error("Failed to update song orders in room %s: %s", roomid, e)
        await session.rollback()
        raise HTTPException(status_code=500,
                            detail=f"Failed to update song orders: {str(e)}") from e  # pylint: disable=raise-missing-from

    # Return updated list
    return await get_room_songs_list(roomid, offset=0, limit=20, session=session)


@room_songs_router.delete("/all", response_model=RoomSongsListResponse)
async def clear_all_room_songs(
    roomid: str, http_request: Request, session: AsyncSession = Depends(get_db)
) -> RoomSongsListResponse:
    """Remove all songs from a room."""
    # Check if room exists
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    await _require_room_owner(session, http_request, roomid)
    await _require_room_waiting(session, roomid)

    # Clear all songs
    try:
        removed_count = await crud.clear_room_songs(session, roomid)
        await session.commit()
        logger.info("Cleared %s songs from room %s", removed_count, roomid)
    except Exception as e:
        logger.error("Failed to clear songs from room %s: %s", roomid, e)
        await session.rollback()
        raise HTTPException(status_code=500,
                            detail=f"Failed to clear songs from room: {str(e)}") from e  # pylint: disable=raise-missing-from

    # Return empty list
    return RoomSongsListResponse(room_id=roomid, list=[], total=0)


@room_songs_router.post("/shuffle", response_model=RoomSongsListResponse)
async def shuffle_room_songs_list(
    roomid: str, http_request: Request, session: AsyncSession = Depends(get_db)
) -> RoomSongsListResponse:
    """Manually shuffle songs in a room."""
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    await _require_room_owner(session, http_request, roomid)
    await _require_room_waiting(session, roomid)

    try:
        await crud.shuffle_room_songs(session, roomid)
        await _refresh_default_playback_initial_song(session, roomid)
        await session.commit()
        await _trigger_preload_top_songs(session, roomid)
        logger.info("Manually shuffled songs in room %s", roomid)
    except Exception as e:
        logger.error("Failed to shuffle songs in room %s: %s", roomid, e)
        await session.rollback()
        raise HTTPException(status_code=500,
                            detail=f"Failed to shuffle songs: {str(e)}") from e  # pylint: disable=raise-missing-from

    return await get_room_songs_list(roomid, offset=0, limit=20, session=session)


@room_songs_router.get("/{songid}")
async def get_room_song_detail(
    roomid: str, songid: int,
    session: AsyncSession = Depends(get_db)) -> RoomSongResponse:
    """Get details of a specific song in a room."""
    # Check if room exists
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Get room song
    room_song = await crud.get_room_song(session, roomid, songid)
    if not room_song:
        raise HTTPException(status_code=404,
                            detail=f"Song with ID {songid} not found in room")

    # Get the associated song details
    song_stmt = select(models.Song).where(models.Song.id == songid)
    song_result = await session.execute(song_stmt)
    song = song_result.scalar_one_or_none()

    if not song:
        raise HTTPException(status_code=404, detail=f"Song with ID {songid} not found")

    # Convert SQLAlchemy Song object to dictionary for SongResponse
    song_response = SongResponse.model_validate(song)

    return RoomSongResponse(
        room_id=room_song.room_id,
        song_id=room_song.song_id,
        song_order=room_song.song_order,
        song=song_response,
    )
