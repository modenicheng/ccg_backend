from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from db.crud import create_task_record, get_task_record_by_task_id
from db.models import Songlist, SonglistSong, Tasks
from db.session import get_db
from schemas.songlist import SonglistBase, SonglistListResponse, SconglistFromMidRequest, SonglistResponse
from schemas.song import TaskResponse

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


@songlist_router.get("/", response_model=SonglistListResponse)
async def songlist_list(offset: int = Query(default=0, ge=0),
                        limit: int = Query(default=20, ge=1, le=100),
                        kw: str | None = Query(default=None, max_length=100),
                        session: AsyncSession = Depends(get_db)):
    stmt = select(Songlist).offset(offset).limit(limit).options(
        selectinload(Songlist.songs).selectinload(SonglistSong.song))
    if kw:
        stmt = stmt.where(Songlist.title.ilike(f"%{kw}%"))
    result = await session.execute(stmt)
    # 统计 Songlist 总数
    count_stmt = select(func.count()).select_from(Songlist)
    total = (await session.execute(count_stmt)).scalar()
    songlists = result.scalars().all()
    logger.info("Fetched %d songlists (offset=%d, limit=%d, kw=%s)",
                len(songlists), offset, limit, kw)
    return SonglistListResponse(
        total=total,
        songlists=[
            SonglistResponse(
                **SonglistBase.model_validate(songlist).model_dump(),
                id=songlist.id,
                count=len(songlist.songs)
            )
            for songlist in songlists
        ]
    )
    # return songlists


@songlist_router.post("/")
async def create_songlist_from_mid(data: SconglistFromMidRequest,
                                   session: AsyncSession = Depends(get_db)):
    if data.platform == MusicPlatform.QQ:
        task_id = str(uuid4())
        task = tasks.fetch_songlist.task_class(
            args=(int(data.platform_songlist_id), ),
            kwargs={
                "cookie_str": data.cookie_str,
                "task_id": task_id,
            },
            id=task_id,
        )
        tasks.huey.enqueue(task)
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
    task_record = await get_task_record_by_task_id(session=session,
                                                   task_id=task_id)
    if not task_record:
        raise HTTPException(status_code=404, detail="Task not found")
    return await _build_task_response(session=session, task_record=task_record)
