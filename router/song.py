from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from db.models import Song
from db.session import get_db
from schemas.song import SongResponse, SongCreate, SongListResponse

song_router = APIRouter(prefix="/api/songs", tags=["songs"])


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
        SongResponse.model_validate({
            c.name: getattr(song, c.name)
            for c in song.__table__.columns
        })
        for song in songs
    ]
    return SongListResponse(total=total, list=song_list_data)


@song_router.post("/", response_model=SongResponse)
async def create_song(
    song_data: SongCreate,
    session: AsyncSession = Depends(get_db)
):
    # 检查是否已存在相同的平台歌曲ID
    if song_data.platform_song_id:
        stmt = select(Song).where(Song.platform_song_id == song_data.platform_song_id)
        existing = await session.execute(stmt)
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Song with this platform ID already exists")

    song = Song(**song_data.model_dump(exclude_unset=True))
    session.add(song)
    await session.commit()
    await session.refresh(song)
    # Convert SQLAlchemy object to dictionary to avoid async context issues
    return SongResponse.model_validate({c.name: getattr(song, c.name) for c in song.__table__.columns})


@song_router.get("/{song_id}", response_model=SongResponse)
async def get_song(
    song_id: int,
    session: AsyncSession = Depends(get_db)
):
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    # Convert SQLAlchemy object to dictionary to avoid async context issues
    return SongResponse.model_validate({c.name: getattr(song, c.name) for c in song.__table__.columns})


@song_router.put("/{song_id}", response_model=SongResponse)
async def update_song(
    song_id: int,
    song_data: SongCreate,
    session: AsyncSession = Depends(get_db)
):
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
    return SongResponse.model_validate({c.name: getattr(song, c.name) for c in song.__table__.columns})


@song_router.delete("/{song_id}")
async def delete_song(
    song_id: int,
    session: AsyncSession = Depends(get_db)
):
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    await session.delete(song)
    await session.commit()
    return {"message": "Song deleted successfully"}
