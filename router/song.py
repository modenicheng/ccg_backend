"""Song management endpoints."""

from __future__ import annotations
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select, func, exists
from sqlalchemy.ext.asyncio import AsyncSession
from cache.file_cache import build_file_response
from config import app_config
from db.crud import authenticate_user_global
from db.models import (
    Song,
    Room,
    RoomSong,
    SongTagHistory,
    Tag,
    TagGroup,
    TagGroupTag,
    User,
)
from db.session import get_db
from schemas.song import (
    SongResponse,
    SongCreate,
    SongListResponse,
    SongTagHistorySummaryResponse,
    SongTagGroupHistoryItem,
    SongTagHistoryOption,
    SongTagHistoryDetailResponse,
    SongTagHistoryRecord,
)
from mq import tasks
from utils import get_logger
from utils.enumerations import RoomStatus

logger = get_logger(__name__)

song_router = APIRouter(prefix="/api/songs", tags=["songs"])


async def _require_auth(
        request: Request,
        session: AsyncSession = Depends(get_db),
) -> User:
    """验证请求用户身份（全局 CRUD 端点通用鉴权）。"""
    token = request.query_params.get("token")
    user_id_raw = request.query_params.get("user_id")
    if not token or not user_id_raw:
        raise HTTPException(status_code=403, detail="Authentication required")
    try:
        user_id = int(user_id_raw)
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid user_id")
    user = await authenticate_user_global(session, token, user_id)
    if not user:
        raise HTTPException(status_code=403, detail="Authentication failed")
    return user


@song_router.get("/", response_model=SongListResponse)
async def song_list(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
        kw: str | None = Query(default=None, max_length=100),
        session: AsyncSession = Depends(get_db),
) -> SongListResponse:
    """Get paginated list of songs with optional keyword search."""
    stmt = select(Song).offset(offset).limit(limit)
    if kw:
        stmt = stmt.where(Song.title.ilike(f"%{kw}%"))

    count_stmt = select(func.count()).select_from(Song)  # pylint: disable=not-callable
    if kw:
        count_stmt = count_stmt.where(Song.title.ilike(f"%{kw}%"))

    logger.info("Fetching songs with offset=%s, limit=%s, kw=%s", offset, limit, kw)
    result = await session.execute(stmt)
    count_result = await session.execute(count_stmt)
    songs = result.scalars().all()
    total = count_result.scalar() or 0
    logger.info("Found %s songs (total: %s)", len(songs), total)

    song_list_data = [
        SongResponse.model_validate(
            {c.name: getattr(song, c.name)
             for c in song.__table__.columns})
        for song in songs
    ]
    return SongListResponse(total=total, list=song_list_data)


@song_router.post("/", response_model=SongResponse)
async def create_song(
        song_data: SongCreate,
        session: AsyncSession = Depends(get_db),
        _auth: User = Depends(_require_auth),
) -> SongResponse:
    """Create a new song."""
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
    logger.info("Creating song with data: %s", dump_data)

    song = Song(**dump_data)
    session.add(song)
    try:
        await session.commit()
        await session.refresh(song)
        logger.info("Song created successfully with id: %s", song.id)
    except Exception as e:
        logger.error("Failed to create song: %s", e, exc_info=True)
        await session.rollback()
        raise HTTPException(status_code=500, detail="Failed to create song") from e
    # Convert SQLAlchemy object to dictionary to avoid async context issues
    return SongResponse.model_validate(
        {c.name: getattr(song, c.name) for c in song.__table__.columns})


@song_router.get("/{song_id}", response_model=SongResponse)
async def get_song(
    song_id: int, session: AsyncSession = Depends(get_db)) -> SongResponse:
    """Get a song by ID."""
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    # Convert SQLAlchemy object to dictionary to avoid async context issues
    return SongResponse.model_validate(
        {c.name: getattr(song, c.name) for c in song.__table__.columns})


@song_router.get("/{song_id}/history/tags",
                 response_model=SongTagHistorySummaryResponse)
async def get_song_tag_history_summary(
        song_id: int,
        session: AsyncSession = Depends(get_db),
) -> SongTagHistorySummaryResponse:
    """Get aggregated historical correct-tag options for a song."""
    song_stmt = select(Song.id).where(Song.id == song_id)
    song_result = await session.execute(song_stmt)
    if song_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Song not found")

    selected_count_expr = func.count(SongTagHistory.id).label("selected_count")
    summary_stmt = (select(
        TagGroup.id.label("group_id"),
        TagGroup.name.label("group_name"),
        Tag.id.label("tag_id"),
        Tag.name.label("tag_name"),
        selected_count_expr,
    ).select_from(SongTagHistory).join(Tag, SongTagHistory.tag_id == Tag.id).join(
        TagGroupTag, TagGroupTag.tag_id == Tag.id).join(
            TagGroup, TagGroup.id == TagGroupTag.group_id).where(
                SongTagHistory.song_id == song_id).group_by(
                    TagGroup.id,
                    TagGroup.name,
                    Tag.id,
                    Tag.name,
                ).order_by(
                    TagGroup.id.asc(),
                    selected_count_expr.desc(),
                    Tag.id.asc(),
                ))

    rows = (await session.execute(summary_stmt)).all()

    grouped: dict[int, SongTagGroupHistoryItem] = {}
    for row in rows:
        if row.group_id not in grouped:
            grouped[row.group_id] = SongTagGroupHistoryItem(
                group_id=row.group_id,
                group_name=row.group_name,
                tags=[],
            )

        grouped[row.group_id].tags.append(
            SongTagHistoryOption(tag_id=row.tag_id,
                                 tag_name=row.tag_name,
                                 selected_count=int(row.selected_count or 0)))

    return SongTagHistorySummaryResponse(song_id=song_id, groups=list(grouped.values()))


@song_router.get("/{song_id}/history/tags/{tag_id}/records",
                 response_model=SongTagHistoryDetailResponse)
async def get_song_tag_history_records(
        song_id: int,
        tag_id: int,
        group_id: int | None = Query(default=None, ge=1),
        session: AsyncSession = Depends(get_db),
) -> SongTagHistoryDetailResponse:
    """Get all historical records for one song+tag selection."""
    song_stmt = select(Song.id).where(Song.id == song_id)
    song_result = await session.execute(song_stmt)
    if song_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Song not found")

    tag_stmt = select(Tag.id, Tag.name).where(Tag.id == tag_id)
    tag_result = await session.execute(tag_stmt)
    tag_row = tag_result.one_or_none()
    if tag_row is None:
        raise HTTPException(status_code=404, detail="Tag not found")

    group_row = None
    if group_id is not None:
        group_stmt = (select(TagGroup.id, TagGroup.name).join(
            TagGroupTag, TagGroupTag.group_id == TagGroup.id).where(
                TagGroup.id == group_id,
                TagGroupTag.tag_id == tag_id,
            ))
        group_result = await session.execute(group_stmt)
        group_row = group_result.one_or_none()
        if group_row is None:
            raise HTTPException(status_code=404,
                                detail="Tag is not in the specified tag group")
    else:
        first_group_stmt = (select(TagGroup.id, TagGroup.name).join(
            TagGroupTag, TagGroupTag.group_id == TagGroup.id).where(
                TagGroupTag.tag_id == tag_id,).order_by(TagGroup.id.asc()).limit(1))
        first_group_result = await session.execute(first_group_stmt)
        group_row = first_group_result.one_or_none()

    records_stmt = (select(
        SongTagHistory.id.label("history_id"),
        SongTagHistory.room_id,
        SongTagHistory.judged_by_user_id,
        User.username.label("judged_by_username"),
        SongTagHistory.created_at,
    ).select_from(SongTagHistory).outerjoin(
        User, User.id == SongTagHistory.judged_by_user_id).where(
            SongTagHistory.song_id == song_id,
            SongTagHistory.tag_id == tag_id,
        ).order_by(
            SongTagHistory.created_at.desc(),
            SongTagHistory.id.desc(),
        ))
    records_result = await session.execute(records_stmt)
    records_rows = records_result.all()

    if not records_rows:
        raise HTTPException(status_code=404,
                            detail="No history records found for this song tag")

    records = [
        SongTagHistoryRecord(
            history_id=row.history_id,
            room_id=row.room_id,
            judged_by_user_id=row.judged_by_user_id,
            judged_by_username=row.judged_by_username,
            created_at=row.created_at,
        ) for row in records_rows
    ]

    return SongTagHistoryDetailResponse(
        song_id=song_id,
        tag_id=tag_row.id,
        tag_name=tag_row.name,
        group_id=group_row.id if group_row else None,
        group_name=group_row.name if group_row else None,
        total=len(records),
        records=records,
    )


@song_router.put("/{song_id}", response_model=SongResponse)
async def update_song(
        song_id: int,
        song_data: SongCreate,
        session: AsyncSession = Depends(get_db),
        _auth: User = Depends(_require_auth),
) -> SongResponse:
    """Update an existing song."""
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
async def delete_song(
        song_id: int,
        session: AsyncSession = Depends(get_db),
        _auth: User = Depends(_require_auth),
):
    """Delete a song by ID."""
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    # Check if song belongs to any RUNNING room
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
        session: AsyncSession = Depends(get_db),
        _auth: User = Depends(_require_auth),
) -> Response:
    """Get cached audio asset for a song."""
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    if not song.cached_path:
        raise HTTPException(status_code=404,
                            detail="Cached path not found for this song")
    content_disposition = (f'inline; filename="{os.path.basename(song.cached_path)}"')
    return build_file_response(song.cached_path, content_disposition)


BASE_ASSETS_PATH = app_config.audio_download_dir


@song_router.post("/cache/{song_id}")
async def cache_song_asset(
        song_id: int,
        session: AsyncSession = Depends(get_db),
        _auth: User = Depends(_require_auth),
):
    """Trigger caching of audio asset for a song."""
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    if song.cached_path and os.path.isfile(song.cached_path):
        raise HTTPException(status_code=400, detail="Song is already cached")

    tasks.download_and_cache_song(mid=song.platform_song_id)
    return {"message": "Caching task started"}
