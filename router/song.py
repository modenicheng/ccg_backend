from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from db.models import Song
from db.session import get_db

song_router = APIRouter(prefix="/api/songs", tags=["songs"])


@song_router.get("/")
async def song_list(offset: int = Query(default=0, ge=0),
                    limit: int = Query(default=20, ge=1, le=100),
                    kw: str | None = Query(default=None, max_length=100),
                    session: AsyncSession = Depends(get_db)):
    stmt = select(Song).offset(offset).limit(limit)
    if kw:
        stmt = stmt.where(Song.title.ilike(f"%{kw}%"))
    result = await session.execute(stmt)
    songs = result.scalars().all()
    # return songs
