from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from db.models import Songlist
from db.session import get_db

songlist_router = APIRouter(prefix="/api/songlists", tags=["songlists"])

@songlist_router.get("/")
async def songlist_list(offset: int = Query(default=0, ge=0),
                        limit: int = Query(default=20, ge=1, le=100),
                        kw: str | None = Query(default=None, max_length=100),
                        session: AsyncSession = Depends(get_db)):
    stmt = select(Songlist).offset(offset).limit(limit)
    if kw:
        stmt = stmt.where(Songlist.title.ilike(f"%{kw}%"))
    result = await session.execute(stmt)
    songlists = result.scalars().all()
    # return songlists
