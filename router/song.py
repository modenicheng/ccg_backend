import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from cache.file_cache import load_song_asset_with_cache
from db.models import Song
from db.session import get_db
from schemas.song import SongResponse, SongCreate, SongListResponse
from mq import tasks

song_router = APIRouter(prefix="/api/songs", tags=["songs"])


def _build_range_response(content: bytes, media_type: str,
                          range_header: str | None) -> Response:
    total = len(content)
    common_headers = {
        "Accept-Ranges": "bytes",
    }

    if not range_header:
        return Response(content=content,
                        media_type=media_type,
                        headers={
                            **common_headers, "Content-Length": str(total)
                        })

    if not range_header.startswith("bytes="):
        raise HTTPException(status_code=416, detail="Invalid Range header")

    range_spec = range_header.replace("bytes=", "", 1).strip()
    if "," in range_spec:
        raise HTTPException(status_code=416,
                            detail="Multiple ranges are not supported")

    start_str, sep, end_str = range_spec.partition("-")
    if sep != "-":
        raise HTTPException(status_code=416, detail="Invalid Range header")

    try:
        if start_str == "":
            # suffix-byte-range-spec: bytes=-500 (最后500字节)
            suffix_length = int(end_str)
            if suffix_length <= 0:
                raise ValueError
            start = max(total - suffix_length, 0)
            end = total - 1
        else:
            start = int(start_str)
            if end_str == "":
                end = total - 1
            else:
                end = int(end_str)
    except ValueError as e:
        raise HTTPException(status_code=416,
                            detail="Invalid Range header") from e

    if total == 0 or start < 0 or end < start or start >= total:
        return Response(status_code=416,
                        headers={
                            "Content-Range": f"bytes */{total}",
                            **common_headers
                        })

    end = min(end, total - 1)
    partial = content[start:end + 1]
    return Response(content=partial,
                    status_code=206,
                    media_type=media_type,
                    headers={
                        **common_headers, "Content-Range":
                        f"bytes {start}-{end}/{total}",
                        "Content-Length": str(len(partial))
                    })


@song_router.get("/", response_model=SongListResponse)
async def song_list(offset: int = Query(default=0, ge=0),
                    limit: int = Query(default=20, ge=1, le=100),
                    kw: str | None = Query(default=None, max_length=100),
                    session: AsyncSession = Depends(get_db)):
    stmt = select(Song).offset(offset).limit(limit)
    if kw:
        stmt = stmt.where(Song.title.ilike(f"%{kw}%"))

    count_stmt = select(func.count()).select_from(Song)
    if kw:
        count_stmt = count_stmt.where(Song.title.ilike(f"%{kw}%"))

    result = await session.execute(stmt)
    count_result = await session.execute(count_stmt)
    songs = result.scalars().all()
    total = count_result.scalar() or 0

    song_list_data = [
        SongResponse.model_validate(
            {c.name: getattr(song, c.name)
             for c in song.__table__.columns}) for song in songs
    ]
    return SongListResponse(total=total, list=song_list_data)


@song_router.post("/", response_model=SongResponse)
async def create_song(song_data: SongCreate,
                      session: AsyncSession = Depends(get_db)):
    # 检查是否已存在相同的平台歌曲ID
    if song_data.platform_song_id:
        stmt = select(Song).where(
            Song.platform_song_id == song_data.platform_song_id)
        existing = await session.execute(stmt)
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Song with this platform ID already exists")

    song = Song(**song_data.model_dump(exclude_unset=True))
    session.add(song)
    await session.commit()
    await session.refresh(song)
    # Convert SQLAlchemy object to dictionary to avoid async context issues
    return SongResponse.model_validate(
        {c.name: getattr(song, c.name)
         for c in song.__table__.columns})


@song_router.get("/{song_id}", response_model=SongResponse)
async def get_song(song_id: int, session: AsyncSession = Depends(get_db)):
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    # Convert SQLAlchemy object to dictionary to avoid async context issues
    return SongResponse.model_validate(
        {c.name: getattr(song, c.name)
         for c in song.__table__.columns})


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
        {c.name: getattr(song, c.name)
         for c in song.__table__.columns})


@song_router.delete("/{song_id}")
async def delete_song(song_id: int, session: AsyncSession = Depends(get_db)):
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

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
    response = _build_range_response(content, media_type, range_header)
    response.headers["Content-Disposition"] = (
        f'inline; filename="{os.path.basename(song.cached_path)}"')
    return response


BASE_ASSETS_PATH = os.getenv("CCG_AUDIO_DOWNLOAD_DIR", "assets/audio")


@song_router.post("/cache/{song_id}")
async def cache_song_asset(song_id: int,
                           session: AsyncSession = Depends(get_db)):
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    if song.cached_path and os.path.isfile(song.cached_path):
        raise HTTPException(status_code=400, detail="Song is already cached")

    tasks.download_and_cache_song(mid=song.platform_song_id)
    return {"message": "Caching task started"}
