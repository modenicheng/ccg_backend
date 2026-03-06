from __future__ import annotations
import asyncio
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import ORJSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, load_only
from db.crud import create_task_record, get_task_record_by_task_id, count_songlist_songs
from db.models import Songlist, SonglistSong, Tasks, Song
from db.session import get_db
from schemas.songlist import (
    SonglistBase,
    SonglistListResponse,
    SonglistFromMidRequest,
    SonglistResponse,
)
from schemas.song import SongResponse, TaskResponse

from utils import get_logger
from utils.enumerations import MusicPlatform

from mq import tasks

logger = get_logger(__name__)

songlist_router = APIRouter(prefix="/api/songlists", tags=["songlists"])


async def _build_task_response(session: AsyncSession,
                               task_record: Tasks) -> TaskResponse:
    await session.refresh(task_record)
    return TaskResponse(
        task_id=task_record.task_id,
        task_name=task_record.task_name,
        status=task_record.status,
        result=task_record.result_json,
        created_at=task_record.created_at,
        updated_at=task_record.updated_at,
    )


@songlist_router.get("/",
                     response_model=SonglistListResponse,
                     response_class=ORJSONResponse)
async def songlist_list(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
        kw: str | None = Query(default=None, max_length=100),
        session: AsyncSession = Depends(get_db),
):
    # 子查询统计每个歌单的歌曲数量
    song_count_subq = (select(func.count(
        SonglistSong.song_id).label("song_count")).where(
            SonglistSong.songlist_id == Songlist.id).scalar_subquery())

    # 主查询，不再加载歌曲关系
    stmt = (select(Songlist,
                   song_count_subq.label("song_count")).offset(offset).limit(limit))

    if kw:
        stmt = stmt.where(Songlist.title.ilike(f"%{kw}%"))

    # 统计 Songlist 总数
    count_stmt = select(func.count()).select_from(Songlist)
    if kw:
        count_stmt = count_stmt.where(Songlist.title.ilike(f"%{kw}%"))

    data_result = await session.execute(stmt)
    count_result = await session.execute(count_stmt)

    rows = data_result.all()
    total = count_result.scalar() or 0

    songlists = []
    for row in rows:
        songlist, song_count = row
        songlists.append({"songlist": songlist, "song_count": song_count or 0})

    logger.info(
        "Fetched %d songlists (offset=%d, limit=%d, kw=%s)",
        len(songlists),
        offset,
        limit,
        kw,
    )
    return SonglistListResponse(
        total=total,
        list=[
            SonglistResponse(
                id=songlist["songlist"].id,
                title=songlist["songlist"].title,
                cover_url=songlist["songlist"].cover_url,
                platform=songlist["songlist"].platform,
                platform_songlist_id=songlist["songlist"].platform_songlist_id,
                count=songlist["song_count"],
            ) for songlist in songlists
        ],
    )


@songlist_router.post("/")
async def create_songlist_from_mid(data: SonglistFromMidRequest,
                                   session: AsyncSession = Depends(get_db)):
    if data.platform == MusicPlatform.QQ:
        task_id = str(uuid4())
        task = tasks.fetch_songlist.task_class(
            args=(int(data.platform_songlist_id),),
            kwargs={
                "cookie_str": data.cookie_str,
                "task_id": task_id,
            },
            id=task_id,
        )
        tasks.huey.enqueue(task)
        try:
            await create_task_record(
                session=session,
                task_id=task_id,
                task_name="fetch_songlist",
                status="pending",
                result_json={
                    "platform": data.platform.value,
                    "platform_songlist_id": data.platform_songlist_id,
                },
            )
            await session.commit()
            logger.info(f"Created task record for songlist fetch: {task_id}")
        except Exception as e:
            logger.error(f"Failed to create task record: {e}")
            await session.rollback()
            raise HTTPException(status_code=500,
                                detail=f"Failed to create task record: {str(e)}")
        return TaskResponse(
            task_id=task_id,
            task_name="fetch_songlist",
            status="pending",
            huey_task_id=task_id,
            result=None,
            created_at=None,
            updated_at=None,
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported platform")


@songlist_router.get("/task/{task_id}")
async def get_songlist_task_result(task_id: str,
                                   session: AsyncSession = Depends(get_db)):
    task_record = await get_task_record_by_task_id(session=session, task_id=task_id)
    if not task_record:
        raise HTTPException(status_code=404, detail="Task not found")
    return await _build_task_response(session=session, task_record=task_record)


@songlist_router.get("/{songlist_id}", response_model=SonglistResponse)
async def get_songlist_detail(songlist_id: int,
                              session: AsyncSession = Depends(get_db)):
    # 使用 load_only 限制加载的 Song 字段
    stmt = (select(Songlist).where(Songlist.id == songlist_id).options(
        selectinload(Songlist.songs).selectinload(SonglistSong.song).load_only(
            Song.id,
            Song.title,
            Song.subtitle,
            Song.artist,
            Song.cover_url,
            Song.cached_path,
            Song.platform,
            Song.platform_song_id,
            Song.album_name,
            Song.album_id,
        )))

    songlist_result = await session.execute(stmt)

    songlist = songlist_result.scalar_one_or_none()
    if not songlist:
        raise HTTPException(status_code=404, detail="Songlist not found")

    song_count = await count_songlist_songs(session, songlist_id) or 0

    # Convert SQLAlchemy Song objects to dictionaries within the async session context
    song_responses = [
        SongResponse.model_validate(song_list_song.song)
        for song_list_song in songlist.songs
    ]

    return SonglistResponse(
        id=songlist.id,
        title=songlist.title,
        cover_url=songlist.cover_url,
        platform=songlist.platform,
        platform_songlist_id=songlist.platform_songlist_id,
        count=song_count,
        songs=song_responses,
    )


@songlist_router.put("/{songlist_id}",
                     response_model=SonglistResponse,
                     response_class=ORJSONResponse)
async def update_songlist(
        songlist_id: int,
        songlist_data: SonglistBase,
        session: AsyncSession = Depends(get_db),
):
    stmt = select(Songlist).where(Songlist.id == songlist_id)
    result = await session.execute(stmt)
    songlist = result.scalar_one_or_none()
    if not songlist:
        raise HTTPException(status_code=404, detail="Songlist not found")

    # 更新字段
    for field, value in songlist_data.model_dump(exclude_unset=True).items():
        setattr(songlist, field, value)

    await session.commit()
    await session.refresh(songlist)
    # 重新加载歌曲关系
    await session.refresh(songlist, attribute_names=["songs"])
    songs = [song.song for song in songlist.songs]
    # Convert SQLAlchemy Song objects to dictionaries to avoid async context issues
    song_responses = []
    for song in songs:
        song_dict = {c.name: getattr(song, c.name) for c in song.__table__.columns}
        song_responses.append(SongResponse.model_validate(song_dict))

    return SonglistResponse(
        id=songlist.id,
        title=songlist.title,
        cover_url=songlist.cover_url,
        platform=songlist.platform,
        platform_songlist_id=songlist.platform_songlist_id,
        count=len(songs),
        songs=song_responses,
    )


@songlist_router.delete("/{songlist_id}")
async def delete_songlist(songlist_id: int, session: AsyncSession = Depends(get_db)):
    stmt = select(Songlist).where(Songlist.id == songlist_id)
    result = await session.execute(stmt)
    songlist = result.scalar_one_or_none()
    if not songlist:
        raise HTTPException(status_code=404, detail="Songlist not found")

    # 需要先删除关联的 SonglistSong 记录（级联删除可能已处理）
    await session.delete(songlist)
    await session.commit()
    return {"message": "Songlist deleted successfully"}
