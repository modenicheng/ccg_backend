from __future__ import annotations
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from cache.file_cache import load_song_asset_with_cache
from config import app_config
from db.models import Song, Room, RoomSong
from db.session import get_db
from schemas.song import SongResponse, SongCreate, SongListResponse
from mq import tasks
from utils import get_logger
from utils.http_utils import build_range_response
from utils.enumerations import RoomStatus

logger = get_logger(__name__)

song_router = APIRouter(prefix="/api/songs", tags=["songs"])


@song_router.get("/", response_model=SongListResponse)
async def song_list(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
        kw: str | None = Query(default=None, max_length=100),
        session: AsyncSession = Depends(get_db),
):
    stmt = select(Song).offset(offset).limit(limit)
    if kw:
        stmt = stmt.where(Song.title.ilike(f"%{kw}%"))

    count_stmt = select(func.count()).select_from(Song)
    if kw:
        count_stmt = count_stmt.where(Song.title.ilike(f"%{kw}%"))

    logger.info(f"Fetching songs with offset={offset}, limit={limit}, kw={kw}")
    result = await session.execute(stmt)
    count_result = await session.execute(count_stmt)
    songs = result.scalars().all()
    total = count_result.scalar() or 0
    logger.info(f"Found {len(songs)} songs (total: {total})")

    song_list_data = [
        SongResponse.model_validate(
            {c.name: getattr(song, c.name)
             for c in song.__table__.columns})
        for song in songs
    ]
    return SongListResponse(total=total, list=song_list_data)


@song_router.post("/", response_model=SongResponse)
async def create_song(song_data: SongCreate, session: AsyncSession = Depends(get_db)):
    # 检查是否已存在相同的平台歌曲ID
    if song_data.platform_song_id:
        stmt = select(Song).where(Song.platform_song_id == song_data.platform_song_id)
        existing = await session.execute(stmt)
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400,
                                detail="Song with this platform ID already exists")

    # 基本验证：至少需要标题或平台ID
    if not song_data.title and not song_data.platform_song_id:
        raise HTTPException(
            status_code=400,
            detail="At least one of 'title' or 'platform_song_id' is required",
        )

    # 如果提供了platform_song_id，platform也必须提供
    if song_data.platform_song_id and not song_data.platform:
        raise HTTPException(
            status_code=400,
            detail="'platform' is required when 'platform_song_id' is provided",
        )

    dump_data = song_data.model_dump(exclude_unset=True)
    # 记录即将创建的数据用于调试
    logger.info(f"Creating song with data: {dump_data}")

    song = Song(**dump_data)
    session.add(song)
    try:
        await session.commit()
        await session.refresh(song)
        logger.info(f"Song created successfully with id: {song.id}")
    except Exception as e:
        logger.error(f"Failed to create song: {e}")
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create song: {str(e)}")
    # Convert SQLAlchemy object to dictionary to avoid async context issues
    return SongResponse.model_validate(
        {c.name: getattr(song, c.name) for c in song.__table__.columns})


@song_router.get("/{song_id}", response_model=SongResponse)
async def get_song(song_id: int, session: AsyncSession = Depends(get_db)):
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    # Convert SQLAlchemy object to dictionary to avoid async context issues
    return SongResponse.model_validate(
        {c.name: getattr(song, c.name) for c in song.__table__.columns})


@song_router.put("/{song_id}", response_model=SongResponse)
async def update_song(song_id: int,
                      song_data: SongCreate,
                      session: AsyncSession = Depends(get_db)):
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    # 更新字段
    for field, value in song_data.model_dump(exclude_unset=True).items():
        setattr(song, field, value)

    await session.commit()
    await session.refresh(song)
    # Convert SQLAlchemy object to dictionary to avoid async context issues
    return SongResponse.model_validate(
        {c.name: getattr(song, c.name) for c in song.__table__.columns})


@song_router.delete("/{song_id}")
async def delete_song(song_id: int, session: AsyncSession = Depends(get_db)):
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    # Check if song belongs to any RUNNING room
    from sqlalchemy import exists

    running_room_stmt = select(exists().where(
        RoomSong.song_id == song_id,
        RoomSong.room_id == Room.id,
        Room.status == RoomStatus.RUNNING.value,
    ))
    running_result = await session.execute(running_room_stmt)
    is_in_running_room = running_result.scalar()
    if is_in_running_room:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete song because it belongs to a RUNNING room",
        )

    await session.delete(song)
    await session.commit()
    return {"message": "Song deleted successfully"}


@song_router.get("/cache/{song_id}")
async def get_song_asset(
        song_id: int,
        request: Request,
        session: AsyncSession = Depends(get_db),
) -> Response:
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    if not song.cached_path:
        raise HTTPException(status_code=404,
                            detail="Cached path not found for this song")
    content, media_type = await load_song_asset_with_cache(song.cached_path)
    range_header = request.headers.get("range")
    response = build_range_response(content, media_type, range_header)
    response.headers["Content-Disposition"] = (
        f'inline; filename="{os.path.basename(song.cached_path)}"')
    return response


BASE_ASSETS_PATH = app_config.audio_download_dir


@song_router.post("/cache/{song_id}")
async def cache_song_asset(song_id: int, session: AsyncSession = Depends(get_db)):
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    if song.cached_path and os.path.isfile(song.cached_path):
        raise HTTPException(status_code=400, detail="Song is already cached")

    tasks.download_and_cache_song(mid=song.platform_song_id)
    return {"message": "Caching task started"}
