from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import crud, models
from db.session import get_db
from schemas.room_songs import (
    RoomSongsListResponse,
    RoomSongResponse,
    AddRoomSongsRequest,
    RemoveRoomSongsRequest,
    UpdateRoomSongOrderRequest,
    BatchUpdateRoomSongOrderRequest,
)
from schemas.song import SongResponse
from utils import get_logger

logger = get_logger(__name__)

room_songs_router = APIRouter(prefix="/api/rooms/{roomid}/songs",
                              tags=["room_songs"])


@room_songs_router.get("/", response_model=RoomSongsListResponse)
async def get_room_songs_list(
    roomid: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=1000),
    session: AsyncSession = Depends(get_db)
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
                                           limit=limit)

    total = await crud.count_room_songs(session, roomid) or 0

    # Convert to response format
    song_responses = []
    for rs in room_songs:
        # Convert SQLAlchemy Song object to dictionary for SongResponse
        if hasattr(rs, 'song') and rs.song:
            song_dict = {
                c.name: getattr(rs.song, c.name)
                for c in rs.song.__table__.columns
            }
            song_response = SongResponse.model_validate(song_dict)
        else:
            # If song is not loaded, create a minimal SongResponse
            song_response = SongResponse(id=rs.song_id)

        room_song_response = RoomSongResponse(room_id=rs.room_id,
                                              song_id=rs.song_id,
                                              song_order=rs.song_order,
                                              song=song_response)
        song_responses.append(room_song_response)

    return RoomSongsListResponse(room_id=roomid,
                                 list=song_responses,
                                 total=total)


@room_songs_router.post("/", response_model=RoomSongsListResponse)
async def add_songs_to_room(
    roomid: str,
    request: AddRoomSongsRequest,
    session: AsyncSession = Depends(get_db)
) -> RoomSongsListResponse:
    """Add songs to a room."""
    # Check if room exists
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Check if songs exist
    song_stmt = select(models.Song).where(models.Song.id.in_(request.song_ids))
    song_result = await session.execute(song_stmt)
    existing_songs = list(song_result.scalars().all())
    existing_song_ids = {song.id for song in existing_songs}

    missing_song_ids = [
        sid for sid in request.song_ids if sid not in existing_song_ids
    ]
    if missing_song_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Songs with IDs {missing_song_ids} not found")

    # Add songs to room
    added = await crud.add_songs_to_room(session,
                                         roomid,
                                         request.song_ids,
                                         append_to_end=request.append_to_end)

    # Return updated list
    return await get_room_songs_list(roomid,
                                     offset=0,
                                     limit=20,
                                     session=session)


@room_songs_router.delete("/", response_model=RoomSongsListResponse)
async def remove_songs_from_room(
    roomid: str,
    request: RemoveRoomSongsRequest,
    session: AsyncSession = Depends(get_db)
) -> RoomSongsListResponse:
    """Remove songs from a room."""
    # Check if room exists
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Remove songs
    removed_count = await crud.remove_songs_from_room(session, roomid,
                                                      request.song_ids)

    logger.info(f"Removed {removed_count} songs from room {roomid}")

    # Return updated list
    return await get_room_songs_list(roomid,
                                     offset=0,
                                     limit=20,
                                     session=session)


@room_songs_router.put("/", response_model=RoomSongsListResponse)
async def batch_update_room_song_order(
    roomid: str,
    request: BatchUpdateRoomSongOrderRequest,
    session: AsyncSession = Depends(get_db)
) -> RoomSongsListResponse:
    """Batch update song orders in a room."""
    # Check if room exists
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Validate all songs exist in room
    for order_update in request.orders:
        room_song = await crud.get_room_song(session, roomid,
                                             order_update.song_id)
        if not room_song:
            raise HTTPException(
                status_code=404,
                detail=f"Song with ID {order_update.song_id} not found in room"
            )

    # Apply updates (simple sequential update - for complex reordering,
    # client should send complete new ordering)
    for order_update in request.orders:
        await crud.update_room_song_order(session, roomid,
                                          order_update.song_id,
                                          order_update.new_order)

    # Return updated list
    return await get_room_songs_list(roomid,
                                     offset=0,
                                     limit=20,
                                     session=session)


@room_songs_router.delete("/all", response_model=RoomSongsListResponse)
async def clear_all_room_songs(
    roomid: str,
    session: AsyncSession = Depends(get_db)) -> RoomSongsListResponse:
    """Remove all songs from a room."""
    # Check if room exists
    room_stmt = select(models.Room).where(models.Room.id == roomid)
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # Clear all songs
    removed_count = await crud.clear_room_songs(session, roomid)

    logger.info(f"Cleared {removed_count} songs from room {roomid}")

    # Return empty list
    return RoomSongsListResponse(room_id=roomid, list=[], total=0)


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
        raise HTTPException(status_code=404,
                            detail=f"Song with ID {songid} not found")

    # Convert SQLAlchemy Song object to dictionary for SongResponse
    song_response = SongResponse.model_validate(song)

    return RoomSongResponse(room_id=room_song.room_id,
                            song_id=room_song.song_id,
                            song_order=room_song.song_order,
                            song=song_response)
