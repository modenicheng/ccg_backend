from __future__ import annotations
from typing import Any, Literal, Optional

from sqlalchemy import and_, select, tuple_, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from . import models
from utils.enumerations import MusicPlatform
from utils import get_logger

l = get_logger(__name__)


def _apply_songlist_fields(
    songlist: models.Songlist,
    title: Optional[str],
    creator_name: Optional[str],
    cover_url: Optional[str],
    metadata_json: Optional[dict[str, Any]],
) -> None:
    songlist.title = title
    songlist.creator_name = creator_name
    songlist.cover_url = cover_url
    songlist.metadata_json = metadata_json


def _apply_song_fields(
    song: models.Song,
    title: Optional[str],
    subtitle: Optional[str],
    artist: Optional[str],
    cover_url: Optional[str],
    audio_url: Optional[str],
    cached_path: Optional[str],
    album_name: Optional[str],
    metadata_json: Optional[dict[str, Any]],
) -> None:
    song.title = title
    song.subtitle = subtitle
    song.artist = artist
    song.cover_url = cover_url
    song.cached_path = cached_path
    song.album_name = album_name
    song.metadata_json = metadata_json


def _to_platform_value(platform: Any) -> Optional[str]:
    if isinstance(platform, MusicPlatform):
        return platform.value
    return platform


def _join_singer_names(raw_singers: Any) -> Optional[str]:
    if isinstance(raw_singers, list):
        names = [
            str(item.get("name")) for item in raw_singers
            if isinstance(item, dict) and item.get("name")
        ]
        return "/".join(names) if names else None
    if isinstance(raw_singers, str):
        return raw_singers
    return None


def _normalize_song_input(
        song_data: dict[str, Any]) -> Optional[dict[str, Any]]:
    platform = _to_platform_value(song_data.get("platform") or "qq")

    platform_song_id = (song_data.get("platform_song_id")
                        or song_data.get("songmid") or song_data.get("mid")
                        or song_data.get("id"))
    if platform_song_id is None:
        return None
    platform_song_id = str(platform_song_id)

    album_raw = song_data.get("album")
    album_info: dict[str,
                     Any] = album_raw if isinstance(album_raw, dict) else {}

    return {
        "platform":
        platform,
        "platform_song_id":
        platform_song_id,
        "title":
        song_data.get("title") or song_data.get("songname")
        or song_data.get("name"),
        "subtitle":
        song_data.get("subtitle") or song_data.get("subTitle")
        or song_data.get("subtitle_name"),
        "artist":
        song_data.get("artist") or _join_singer_names(song_data.get("singer")),
        "cover_url":
        song_data.get("cover_url") or song_data.get("cover")
        or song_data.get("picurl"),
        "audio_url":
        song_data.get("audio_url") or song_data.get("url"),
        "cached_path":
        song_data.get("cached_path"),
        "album_name":
        song_data.get("album_name") or song_data.get("albumname")
        or album_info.get("name"),
        "metadata_json":
        song_data.get("metadata_json")
        if "metadata_json" in song_data else song_data,
    }


async def create_or_update_songlist(
    session: AsyncSession,
    platform: Optional[Literal["qq", "netease"]],
    platform_songlist_id: Optional[int | str],
    title: Optional[str] = None,
    creator_name: Optional[str] = None,
    cover_url: Optional[str] = None,
    metadata_json: Optional[dict[str, Any]] = None,
) -> models.Songlist:
    """创建新的歌单记录"""
    normalized_platform_songlist_id = (str(platform_songlist_id)
                                       if platform_songlist_id is not None else
                                       None)

    query = select(models.Songlist).where(
        and_(
            models.Songlist.platform_songlist_id ==
            normalized_platform_songlist_id,
            models.Songlist.platform == platform,
        ))
    res = await session.execute(query)
    existing = res.scalars().first()
    if existing:
        _apply_songlist_fields(
            existing,
            title=title,
            creator_name=creator_name,
            cover_url=cover_url,
            metadata_json=metadata_json,
        )
        await session.flush()
        return existing

    songlist = models.Songlist(
        platform=platform,
        platform_songlist_id=normalized_platform_songlist_id)
    _apply_songlist_fields(
        songlist,
        title=title,
        creator_name=creator_name,
        cover_url=cover_url,
        metadata_json=metadata_json,
    )

    session.add(songlist)
    await session.flush()
    return songlist


async def create_or_update_song(
    session: AsyncSession,
    songlist_id: Optional[int],
    platform: Optional[Literal["qq", "netease"]],
    platform_song_id: Optional[str],
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    artist: Optional[str] = None,
    cover_url: Optional[str] = None,
    audio_url: Optional[str] = None,
    cached_path: Optional[str] = None,
    album_name: Optional[str] = None,
    metadata_json: Optional[dict[str, Any]] = None,
) -> models.Song:
    """创建或更新歌曲记录"""
    query = select(models.Song).where(
        and_(
            models.Song.platform_song_id == platform_song_id,
            models.Song.platform == platform,
        ))
    res = await session.execute(query)
    existing = res.scalars().first()

    async def ensure_songlist_link(song_id: int) -> None:
        if not songlist_id:
            return
        relation_query = select(models.SonglistSong).where(
            and_(
                models.SonglistSong.songlist_id == songlist_id,
                models.SonglistSong.song_id == song_id,
            ))
        relation_res = await session.execute(relation_query)
        relation = relation_res.scalars().first()
        if relation:
            return

        session.add(
            models.SonglistSong(songlist_id=songlist_id, song_id=song_id))

    if existing:
        _apply_song_fields(
            existing,
            title=title,
            subtitle=subtitle,
            artist=artist,
            cover_url=cover_url,
            audio_url=audio_url,
            cached_path=cached_path,
            album_name=album_name,
            metadata_json=metadata_json,
        )
        await ensure_songlist_link(existing.id)
        await session.flush()
        return existing

    song = models.Song(platform=platform, platform_song_id=platform_song_id)
    _apply_song_fields(
        song,
        title=title,
        subtitle=subtitle,
        artist=artist,
        cover_url=cover_url,
        audio_url=audio_url,
        cached_path=cached_path,
        album_name=album_name,
        metadata_json=metadata_json,
    )

    session.add(song)
    await session.flush()
    await ensure_songlist_link(song.id)
    await session.flush()
    return song


async def create_or_update_songs(
        session: AsyncSession, songlist_id: Optional[int],
        songs: list[dict[str, Any]]) -> list[models.Song]:
    """批量创建或更新歌曲记录"""
    if not songs:
        return []

    ordered_keys: list[tuple[str, str]] = []
    song_payload_map: dict[tuple[str, str], dict[str, Any]] = {}

    for raw_song in songs:
        if not isinstance(raw_song, dict):
            continue
        payload = _normalize_song_input(raw_song)
        if not payload:
            continue

        key = (payload["platform"], payload["platform_song_id"])
        if key not in song_payload_map:
            ordered_keys.append(key)
        song_payload_map[key] = payload

    if not ordered_keys:
        return []

    existing_query = select(models.Song).where(
        tuple_(models.Song.platform,
               models.Song.platform_song_id).in_(ordered_keys))
    existing_res = await session.execute(existing_query)
    existing_songs = existing_res.scalars().all()
    song_map: dict[tuple[str, str], models.Song] = {
        (song.platform, song.platform_song_id): song
        for song in existing_songs
        if song.platform is not None and song.platform_song_id is not None
    }

    new_songs: list[models.Song] = []
    result_songs: list[models.Song] = []

    for key in ordered_keys:
        payload = song_payload_map[key]
        existing_song = song_map.get(key)

        if existing_song:
            _apply_song_fields(
                existing_song,
                title=payload["title"],
                subtitle=payload["subtitle"],
                artist=payload["artist"],
                cover_url=payload["cover_url"],
                audio_url=payload["audio_url"],
                cached_path=payload["cached_path"],
                album_name=payload["album_name"],
                metadata_json=payload["metadata_json"],
            )
            result_songs.append(existing_song)
            continue

        song = models.Song(platform=payload["platform"],
                           platform_song_id=payload["platform_song_id"])
        _apply_song_fields(
            song,
            title=payload["title"],
            subtitle=payload["subtitle"],
            artist=payload["artist"],
            cover_url=payload["cover_url"],
            audio_url=payload["audio_url"],
            cached_path=payload["cached_path"],
            album_name=payload["album_name"],
            metadata_json=payload["metadata_json"],
        )
        new_songs.append(song)
        result_songs.append(song)

    if new_songs:
        session.add_all(new_songs)

    await session.flush()

    if songlist_id:
        song_ids = [song.id for song in result_songs if song.id]
        if song_ids:
            relation_query = select(models.SonglistSong).where(
                and_(
                    models.SonglistSong.songlist_id == songlist_id,
                    models.SonglistSong.song_id.in_(song_ids),
                ))
            relation_res = await session.execute(relation_query)
            existing_relations = relation_res.scalars().all()
            existing_song_ids = {
                relation.song_id
                for relation in existing_relations
            }

            new_relations = [
                models.SonglistSong(songlist_id=songlist_id, song_id=song_id)
                for song_id in song_ids if song_id not in existing_song_ids
            ]
            if new_relations:
                session.add_all(new_relations)
                await session.flush()

    return result_songs


async def update_song_cached_path(
    session: AsyncSession,
    platform: Optional[Literal["qq", "netease"]],
    platform_song_id: Optional[str],
    cached_path: Optional[str],
) -> Optional[models.Song]:
    """仅更新歌曲缓存路径，不覆盖其它业务字段。"""
    if not platform or not platform_song_id:
        l.warning(
            f"Missing platform or platform_song_id for cache path update: {platform}, {platform_song_id}, skip updating"
        )
        return None

    query = select(models.Song).where(
        and_(
            models.Song.platform_song_id == platform_song_id,
            models.Song.platform == platform,
        ))
    res = await session.execute(query)
    existing = res.scalars().first()
    if not existing:
        return None

    existing.cached_path = cached_path
    await session.flush()
    return existing


async def create_task_record(
    session: AsyncSession,
    task_id: str,
    task_name: str,
    status: str,
    result_json: Optional[dict[str, Any]] = None,
) -> models.Tasks:
    """创建任务记录。并发场景下使用 UPSERT 防止 task_id 唯一键冲突。"""
    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""

    if dialect_name == "postgresql":
        stmt = (pg_insert(models.Tasks).values(
            task_id=task_id,
            task_name=task_name,
            status=status,
            result_json=result_json,
        ).on_conflict_do_update(
            index_elements=[models.Tasks.task_id],
            set_={
                "task_name": task_name,
                "status": status,
                "result_json": result_json,
            },
        ))
        await session.execute(stmt)
        task = await get_task_record_by_task_id(session=session,
                                                task_id=task_id)
        assert task is not None
        return task

    query = select(models.Tasks).where(models.Tasks.task_id == task_id)
    res = await session.execute(query)
    existing = res.scalars().first()
    if existing:
        existing.task_name = task_name
        existing.status = status
        existing.result_json = result_json
        await session.flush()
        return existing

    task = models.Tasks(task_id=task_id,
                        task_name=task_name,
                        status=status,
                        result_json=result_json)
    session.add(task)
    try:
        await session.flush()
        return task
    except IntegrityError:
        await session.rollback()
        res = await session.execute(query)
        existing = res.scalars().first()
        if not existing:
            raise
        existing.task_name = task_name
        existing.status = status
        existing.result_json = result_json
        await session.flush()
        return existing


async def get_task_record_by_task_id(session: AsyncSession,
                                     task_id: str) -> Optional[models.Tasks]:
    """按 Huey task_id 查询任务记录。"""
    query = select(models.Tasks).where(models.Tasks.task_id == task_id)
    res = await session.execute(query)
    return res.scalars().first()


async def update_task_record(
    session: AsyncSession,
    task_id: str,
    status: Optional[str] = None,
    result_json: Optional[dict[str, Any]] = None,
) -> Optional[models.Tasks]:
    """更新任务状态与结果。"""
    task = await get_task_record_by_task_id(session=session, task_id=task_id)
    if not task:
        return None

    if status is not None:
        task.status = status
    if result_json is not None:
        task.result_json = result_json

    await session.flush()
    return task


# ============================================================================
# Room Songs CRUD Functions
# ============================================================================


async def get_room_songs(
    session: AsyncSession,
    room_id: str,
    offset: int = 0,
    limit: int = 20,
    include_song_details: bool = True,
    order: Literal["+song_order", "-song_order", "+song_id",
                   "-song_id"] = "+song_id",
) -> list[models.RoomSong]:
    """Get all songs associated with a room."""

    stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id)

    if include_song_details:
        stmt = stmt.options(selectinload(models.RoomSong.song))

    # Order by song_order if available, then by song_id
    if "song_order" in order:
        if order.startswith("-"):
            stmt = stmt.order_by(
                models.RoomSong.song_order.desc().nulls_last(),
                models.RoomSong.song_id.desc(),
            )
        else:
            stmt = stmt.order_by(models.RoomSong.song_order.nulls_last(),
                                 models.RoomSong.song_id)
    elif "song_id" in order:
        if order.startswith("-"):
            stmt = stmt.order_by(models.RoomSong.song_id.desc())
        else:
            stmt = stmt.order_by(models.RoomSong.song_id)
    else:
        l.warning(f"Invalid order parameter: {order}, defaulting to +song_id")

    # Apply offset and limit
    stmt = stmt.offset(offset)
    stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def shuffle_room_songs(session: AsyncSession, room_id: str) -> None:
    """Randomly shuffle the order of songs in a room."""
    # Get all songs in the room with their current order
    stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id)
    result = await session.execute(stmt)
    room_songs = list(result.scalars().all())

    if not room_songs:
        return

    import random

    random.shuffle(room_songs)

    # Update song_order based on new shuffled order
    for index, room_song in enumerate(room_songs, start=1):
        room_song.song_order = index

    await session.flush()


async def add_songs_to_room(
        session: AsyncSession,
        room_id: str,
        song_ids: list[int],
        append_to_end: bool = True) -> list[models.RoomSong]:
    """Add songs to a room.

    Args:
        session: Async database session
        room_id: Room ID
        song_ids: List of song IDs to add
        append_to_end: If True, append to end; if False, insert at beginning

    Returns:
        List of created RoomSong associations
    """
    if not song_ids:
        return []

    # Query database to get existing room songs (not just from session)
    stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id)
    result = await session.execute(stmt)
    existing_songs = list(result.scalars().all())
    existing_song_ids = {rs.song_id for rs in existing_songs}

    # Filter out songs already in room
    new_song_ids = [sid for sid in song_ids if sid not in existing_song_ids]
    if not new_song_ids:
        return []

    # Determine order for new songs
    existing_ordered = [
        rs for rs in existing_songs if rs.song_order is not None
    ]
    max_order: int = max(
        [
            rs.song_order
            for rs in existing_ordered if rs.song_order is not None
        ],
        default=0,
    )

    room_songs = []
    for i, song_id in enumerate(new_song_ids):
        song_order = None

        # If there are ordered songs, assign order
        if existing_ordered:
            if append_to_end:
                # Append to end
                song_order = max_order + i + 1
            else:
                # Insert at beginning - need to shift existing orders
                song_order = i + 1

        room_song = models.RoomSong(room_id=room_id,
                                    song_id=song_id,
                                    song_order=song_order)
        room_songs.append(room_song)

    # If inserting at beginning, need to update existing orders
    if not append_to_end and existing_ordered:
        # Shift all existing orders by number of new songs
        for rs in existing_ordered:
            if rs.song_order is not None:
                rs.song_order += len(new_song_ids)

    session.add_all(room_songs)
    await session.flush()
    await shuffle_room_songs(session, room_id)
    room_songs: list[models.RoomSong] = sorted(
        room_songs, key=lambda rs: rs.song_order or 0)
    return room_songs


async def remove_songs_from_room(session: AsyncSession, room_id: str,
                                 song_ids: list[int]) -> int:
    """Remove songs from a room.

    Args:
        session: Async database session
        room_id: Room ID
        song_ids: List of song IDs to remove

    Returns:
        Number of songs removed
    """
    if not song_ids:
        return 0

    stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id,
                                         models.RoomSong.song_id.in_(song_ids))
    result = await session.execute(stmt)
    room_songs = list(result.scalars().all())

    if not room_songs:
        return 0

    # Get ordered songs that will be removed
    removed_ordered = [rs for rs in room_songs if rs.song_order is not None]

    # Delete the associations
    for rs in room_songs:
        await session.delete(rs)

    # If we removed ordered songs, need to reorder remaining songs
    if removed_ordered:
        # Get all remaining ordered songs
        remaining_stmt = (select(models.RoomSong).where(
            models.RoomSong.room_id == room_id,
            models.RoomSong.song_order.is_not(None),
        ).order_by(models.RoomSong.song_order))

        remaining_result = await session.execute(remaining_stmt)
        remaining = list(remaining_result.scalars().all())

        # Reorder sequentially starting from 1
        for i, rs in enumerate(remaining, start=1):
            rs.song_order = i

    await session.flush()
    await shuffle_room_songs(session, room_id)
    return len(room_songs)


async def update_room_song_order(
        session: AsyncSession, room_id: str, song_id: int,
        new_order: int | None) -> models.RoomSong | None:
    """Update the order of a song in a room.

    Args:
        session: Async database session
        room_id: Room ID
        song_id: Song ID
        new_order: New order position (None to remove ordering)

    Returns:
        Updated RoomSong or None if not found
    """
    stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id,
                                         models.RoomSong.song_id == song_id)
    result = await session.execute(stmt)
    room_song = result.scalar_one_or_none()

    if not room_song:
        return None

    old_order = room_song.song_order

    if new_order == old_order:
        return room_song

    # If setting to None (unordered)
    if new_order is None:
        room_song.song_order = None
        # No need to reorder others
        await session.flush()
        return room_song

    # If song was previously unordered
    if old_order is None:
        room_song.song_order = new_order
        # Need to shift other songs to make room
        stmt = select(models.RoomSong).where(
            models.RoomSong.room_id == room_id,
            models.RoomSong.song_order >= new_order,
            models.RoomSong.song_id != song_id,
        )
        result = await session.execute(stmt)
        affected = list(result.scalars().all())

        for rs in affected:
            if rs.song_order is not None:
                rs.song_order += 1

    # If song had an order and is changing
    else:
        # Get all ordered songs in room
        stmt = (select(models.RoomSong).where(
            models.RoomSong.room_id == room_id,
            models.RoomSong.song_order.is_not(None),
        ).order_by(models.RoomSong.song_order))

        result = await session.execute(stmt)
        all_ordered = list(result.scalars().all())

        # Remove the moving song from list temporarily
        moving_song = room_song
        other_ordered = [rs for rs in all_ordered if rs.song_id != song_id]

        # Reorder all songs
        updated = []
        current_order = 1

        for rs in other_ordered:
            if current_order == new_order:
                # Insert moving song here
                moving_song.song_order = current_order
                current_order += 1

            rs.song_order = current_order
            current_order += 1

        # If moving song hasn't been placed yet (moving to end)
        if moving_song.song_order is None:
            moving_song.song_order = current_order

    await session.flush()
    await shuffle_room_songs(session, room_id)
    return room_song


async def clear_room_songs(session: AsyncSession, room_id: str) -> int:
    """Remove all songs from a room.

    Args:
        session: Async database session
        room_id: Room ID

    Returns:
        Number of songs removed
    """
    stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id)
    result = await session.execute(stmt)
    room_songs = list(result.scalars().all())

    if not room_songs:
        return 0

    for rs in room_songs:
        await session.delete(rs)

    await session.flush()
    return len(room_songs)


async def get_room_song(session: AsyncSession, room_id: str,
                        song_id: int) -> models.RoomSong | None:
    """Get a specific room song association."""

    stmt = (select(
        models.RoomSong).where(models.RoomSong.room_id == room_id,
                               models.RoomSong.song_id == song_id).options(
                                   selectinload(models.RoomSong.song)))

    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def count_room_songs(session: AsyncSession, room_id: str) -> int | None:
    """Count total number of songs in a room."""
    stmt = select(func.count()).where(models.RoomSong.room_id == room_id)
    result = await session.execute(stmt)
    return result.scalar()


async def count_songlist_songs(session: AsyncSession,
                               songlist_id: int) -> int | None:
    """Count total number of songs in a songlist."""
    stmt = select(
        func.count()).where(models.SonglistSong.songlist_id == songlist_id)
    result = await session.execute(stmt)
    return result.scalar()


async def simple_authentication(session: AsyncSession, ws_cookie: dict,
                                roomid: str) -> None | models.User:
    l.debug(ws_cookie)

    user_token = ws_cookie.get(f"ccg-room-token:{roomid}")
    user_id = ws_cookie.get(f"ccg-room-user-id:{roomid}")
    username = ws_cookie.get(f"ccg-room-username:{roomid}")

    if not user_token or not user_id or not username:
        l.warning(
            f"WebSocket connection missing authentication cookies for room {roomid}. user_token: {user_token}, user_id: {user_id}, username: {username}"
        )
        return

    stmt = select(models.User).where(models.User.id == int(user_id))
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user or user.token != user_token or user.room_id != roomid:
        l.warning(
            f"WebSocket authentication failed for room {roomid}. user_id: {user_id}, token valid: {user.token == user_token if user else 'N/A'}, room_id valid: {user.room_id == roomid if user else 'N/A'}"
        )
        return

    l.info(
        f"WebSocket connection attempt for room {roomid} with token: {user_token}, user_id: {user_id}, username: {username}"
    )
    return user


async def fetch_room_object(session: AsyncSession,
                            room_id: str) -> models.Room | None:
    stmt = (select(models.Room).where(models.Room.id == room_id).options(
        selectinload(models.Room.users),
        selectinload(models.Room.tag_groups).selectinload(
            models.TagGroup.tags),
    ))

    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ============================================================================
# Judge-related CRUD Functions
# ============================================================================


async def get_room_song_queue(
    session: AsyncSession,
    room_id: str,
) -> list[int]:
    """获取房间的有序歌曲队列（按song_order排序的歌曲ID列表）"""
    stmt = (select(models.RoomSong.song_id).where(
        models.RoomSong.room_id == room_id,
        models.RoomSong.song_order.is_not(None)).order_by(
            models.RoomSong.song_order))

    result = await session.execute(stmt)
    song_ids = result.scalars().all()
    return list(song_ids)


async def get_current_song_info(
    session: AsyncSession,
    room_id: str,
) -> tuple[int | None, int | None]:
    """获取房间的当前歌曲信息

    Returns:
        (song_id, song_index) 元组，如果未设置则返回 (None, None)
    """
    # 获取房间的当前歌曲索引
    stmt = select(
        models.Room.current_song_index).where(models.Room.id == room_id)
    result = await session.execute(stmt)
    song_index = result.scalar_one_or_none()

    if song_index is None:
        return None, None

    # 获取歌曲队列
    song_queue = await get_room_song_queue(session, room_id)

    if not song_queue or song_index < 0 or song_index >= len(song_queue):
        return None, None

    song_id = song_queue[song_index]
    return song_id, song_index


async def get_player_answers_for_judging(
    session: AsyncSession,
    room_id: str,
    song_id: int,
    round_index: int,
) -> dict[int, dict[str, Any]]:
    """获取指定房间、歌曲、轮次的所有玩家答案

    Returns:
        字典，键为玩家ID，值为答案数据
    """

    stmt = (select(models.PlayerAnswer).where(
        models.PlayerAnswer.room_id == room_id,
        models.PlayerAnswer.song_id == song_id,
        models.PlayerAnswer.round_index == round_index,
    ).options(selectinload(models.PlayerAnswer.user)))

    result = await session.execute(stmt)
    answers = result.scalars().all()

    player_answers = {}
    for answer in answers:
        player_answers[answer.user_id] = {
            "user_id": answer.user_id,
            "selected_tag_ids": answer.selected_tag_ids or [],
            "description_text": answer.description_text,
            "answer_order": answer.answer_order,
            "created_at": answer.created_at,
        }

    return player_answers


async def get_tag_group_map(
    session: AsyncSession,
    room_id: str,
) -> dict[int, list[int]]:
    """获取房间的标签组映射

    Returns:
        字典，键为标签组ID，值为该标签组包含的标签ID列表
    """
    # 使用fetch_room_object获取完整的房间对象
    room = await fetch_room_object(session, room_id)
    if not room:
        return {}

    tag_group_map = {}
    for tag_group in room.tag_groups:
        tag_group_map[tag_group.id] = [tag.id for tag in tag_group.tags]

    return tag_group_map


async def update_player_answer_order(
    session: AsyncSession,
    room_id: str,
    song_id: int,
    round_index: int,
    answer_queue: list[str],
) -> int:
    """更新玩家答案的抢答顺序

    Args:
        session: 数据库会话
        room_id: 房间ID
        song_id: 歌曲ID
        round_index: 轮次索引
        answer_queue: 已排序的玩家ID列表（字符串格式）

    Returns:
        更新的记录数
    """
    updated_count = 0

    for order, player_id_str in enumerate(answer_queue, start=1):
        try:
            user_id = int(player_id_str)
        except ValueError:
            continue

        # 查找该玩家的答案记录（最新的）
        stmt = (select(models.PlayerAnswer).where(
            models.PlayerAnswer.room_id == room_id,
            models.PlayerAnswer.user_id == user_id,
            models.PlayerAnswer.song_id == song_id,
            models.PlayerAnswer.round_index == round_index,
        ).order_by(models.PlayerAnswer.created_at.desc()).limit(1))

        result = await session.execute(stmt)
        player_answer = result.scalar_one_or_none()

        if player_answer:
            player_answer.answer_order = order
            updated_count += 1

    await session.flush()
    return updated_count


async def save_score_record(
    session: AsyncSession,
    room_id: str,
    user_id: int,
    round_index: int | None,
    score_delta: int,
    total_score: int | None = None,
) -> models.Score:
    """保存得分记录

    Args:
        session: 数据库会话
        room_id: 房间ID
        user_id: 用户ID
        round_index: 轮次索引
        score_delta: 本轮得分变化
        total_score: 累计总分（如果为None则自动计算）

    Returns:
        创建的Score记录
    """
    if total_score is None:
        # 计算该用户当前累计总分
        stmt = select(func.sum(models.Score.score_delta)).where(
            models.Score.room_id == room_id, models.Score.user_id == user_id)
        result = await session.execute(stmt)
        current_total = result.scalar() or 0
        total_score = current_total + score_delta

    score = models.Score(
        room_id=room_id,
        user_id=user_id,
        round_index=round_index,
        score_delta=score_delta,
        total_score=total_score,
    )

    session.add(score)
    await session.flush()
    return score


# # 辅助函数：获取玩家答案
# async def get_player_answers_for_judging(db: AsyncSession, room_id: str,
#                                          song_id: int, round_index: int):
#     stmt = select(models.PlayerAnswer).where(
#         models.PlayerAnswer.room_id == room_id,
#         models.PlayerAnswer.song_id == song_id,
#         models.PlayerAnswer.round_index == round_index).options(
#             selectinload(models.PlayerAnswer.user))
#     result = await db.execute(stmt)
#     player_answers = result.scalars().all()

#     # 构建答案映射
#     answer_map = {}
#     for answer in player_answers:
#         answer_map[answer.user_id] = {
#             'selected_tag_ids': answer.selected_tag_ids or [],
#             'description_text': answer.description_text,
#             'answer_order': answer.answer_order
#         }

#     return answer_map

# # 辅助函数：获取标签组映射
# async def get_tag_group_map(db: AsyncSession, room_id: str):
#     room = await fetch_room_object(db, room_id)
#     if not room:
#         return {}

#     # 构建标签组到标签的映射
#     tag_group_map = {}
#     for tag_group in room.tag_groups:
#         tag_ids = [tag.id for tag in tag_group.tags]
#         tag_group_map[tag_group.id] = tag_ids

#     return tag_group_map

# # 辅助函数：更新玩家答案顺序
# async def update_player_answer_order(db: AsyncSession, room_id: str,
#                                      song_id: int, round_index: int,
#                                      answer_queue: list[str]):
#     updated_count = 0
#     for order, player_id_str in enumerate(answer_queue, 1):
#         try:
#             player_id = int(player_id_str)
#             stmt = select(models.PlayerAnswer).where(
#                 models.PlayerAnswer.room_id == room_id,
#                 models.PlayerAnswer.song_id == song_id,
#                 models.PlayerAnswer.round_index == round_index,
#                 models.PlayerAnswer.user_id == player_id)
#             result = await db.execute(stmt)
#             answer = result.scalar_one_or_none()
#             if answer:
#                 answer.answer_order = order
#                 updated_count += 1
#         except ValueError:
#             continue
#     return updated_count

# # 辅助函数：保存得分记录
# async def save_score_record(db: AsyncSession, room_id: str, user_id: int,
#                             round_index: int, score_delta: int):
#     # 获取用户当前总分
#     stmt = select(
#         models.Score).where(models.Score.room_id == room_id,
#                             models.Score.user_id == user_id).order_by(
#                                 models.Score.created_at.desc())
#     result = await db.execute(stmt)
#     last_score = result.scalar_one_or_none()

#     if last_score is None or last_score.total_score is None:
#         total_score = score_delta
#     else:
#         total_score = last_score.total_score + score_delta

#     # 创建新的得分记录
#     score = models.Score(room_id=room_id,
#                          user_id=user_id,
#                          round_index=round_index,
#                          score_delta=score_delta,
#                          total_score=total_score)
#     db.add(score)
