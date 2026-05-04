"""Room Songs CRUD Functions"""

from __future__ import annotations

import random
from typing import Literal, Mapping

from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from utils import get_logger

from .. import models

logger = get_logger(__name__)


async def get_room_songs(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    session: AsyncSession,
    room_id: str,
    offset: int = 0,
    limit: int = 20,
    include_song_details: bool = True,
    order: Literal["+song_order", "-song_order", "+song_id", "-song_id"] = "+song_id",
    kw: str | None = None,
) -> list[models.RoomSong]:
    """Get all songs associated with a room."""

    stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id)

    if kw:
        stmt = stmt.join(models.RoomSong.song).where(
            models.Song.title.ilike(f"%{kw}%"),)

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
        logger.warning("Invalid order parameter: %s, defaulting to +song_id", order)

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

    random.shuffle(room_songs)

    # Update song_order based on new shuffled order
    for index, room_song in enumerate(room_songs, start=1):
        room_song.song_order = index

    await session.flush()


async def add_songs_to_room(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
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
    existing_ordered = [rs for rs in existing_songs if rs.song_order is not None]
    max_order: int = max(
        (rs.song_order for rs in existing_ordered if rs.song_order is not None),
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
    room_songs: list[models.RoomSong] = sorted(room_songs,
                                               key=lambda rs: rs.song_order or 0)
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

    removed = []
    removed_ordered = []
    for song_id in song_ids:
        try:
            stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id,
                                                 models.RoomSong.song_id == song_id)
            result = await session.execute(stmt)
            room_song = result.scalar_one_or_none()
            if not room_song:
                continue
            if room_song.song_order is not None:
                removed_ordered.append(room_song)
            await session.delete(room_song)
            removed.append(room_song)
        except (SQLAlchemyError, ValueError) as e:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to remove song %s from room %s: %s", song_id,
                           room_id, e)
            # continue to next song

    if not removed:
        return 0

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
    return len(removed)


async def update_room_song_order(session: AsyncSession, room_id: str, song_id: int,
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

    stmt = (select(models.RoomSong).where(models.RoomSong.room_id == room_id,
                                          models.RoomSong.song_id == song_id).options(
                                              selectinload(models.RoomSong.song)))

    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_room_songs_by_ids(session: AsyncSession, room_id: str,
                                song_ids: list[int]) -> dict[int, models.RoomSong]:
    """Batch-fetch room-song associations by song IDs.

    Returns:
        Dict mapping song_id → RoomSong for songs found in the room.
    """
    if not song_ids:
        return {}
    stmt = (select(models.RoomSong).where(
        models.RoomSong.room_id == room_id,
        models.RoomSong.song_id.in_(song_ids),
    ).options(selectinload(models.RoomSong.song)))
    result = await session.execute(stmt)
    return {rs.song_id: rs for rs in result.scalars().all()}


async def batch_update_room_song_orders(
    session: AsyncSession,
    room_id: str,
    orders: dict[int, int | None],
) -> None:
    """Batch-update song orders in a room using a single query pass.

    Args:
        session: Async database session
        room_id: Room ID
        orders: Mapping of song_id → new song_order value
    """
    song_ids = list(orders.keys())
    stmt = select(models.RoomSong).where(
        models.RoomSong.room_id == room_id,
        models.RoomSong.song_id.in_(song_ids),
    )
    result = await session.execute(stmt)
    room_songs = list(result.scalars().all())
    for rs in room_songs:
        rs.song_order = orders[rs.song_id]
    await session.flush()


async def count_room_songs(session: AsyncSession,
                           room_id: str,
                           kw: str | None = None) -> int | None:
    """Count total number of songs in a room."""
    if kw:
        stmt = (  # pylint: disable=not-callable
            select(func.count(1)).select_from(models.RoomSong).join(
                models.RoomSong.song).where(models.RoomSong.room_id == room_id).where(
                    models.Song.title.ilike(f"%{kw}%")))
    else:
        stmt = select(func.count(1)).where(models.RoomSong.room_id == room_id)  # pylint: disable=not-callable
    result = await session.execute(stmt)
    return result.scalar()


async def count_songlist_songs(session: AsyncSession, songlist_id: int) -> int | None:
    """Count total number of songs in a songlist."""
    stmt = select(func.count(1)).where(models.SonglistSong.songlist_id == songlist_id)  # pylint: disable=not-callable
    result = await session.execute(stmt)
    return result.scalar()


async def simple_authentication(session: AsyncSession, ws_cookie: dict,
                                roomid: str) -> None | models.User:
    """简单WebSocket认证，基于cookie中的房间令牌和用户ID。"""
    logger.debug(ws_cookie)

    user_token = ws_cookie.get(f"ccg-room-token:{roomid}")
    user_id = ws_cookie.get(f"ccg-room-user-id:{roomid}")
    username = ws_cookie.get(f"ccg-room-username:{roomid}")

    if not user_token or not user_id or not username:
        logger.warning(
            "WebSocket connection missing authentication cookies for room %s. "
            "user_token: %s, user_id: %s, username: %s",
            roomid,
            user_token,
            user_id,
            username,
        )
        return

    stmt = select(models.User).where(models.User.id == int(user_id))
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user or user.token != user_token or user.room_id != roomid:
        logger.warning(
            "WebSocket authentication failed for room %s. user_id: %s, "
            "token valid: %s, room_id valid: %s",
            roomid,
            user_id,
            user.token == user_token if user else "N/A",
            user.room_id == roomid if user else "N/A",
        )
        return

    logger.info(
        "WebSocket connection attempt for room %s with token: %s, "
        "user_id: %s, username: %s",
        roomid,
        user_token,
        user_id,
        username,
    )
    return user


async def authenticate_user_by_room_token(
    session: AsyncSession,
    roomid: str,
    user_token: str | None,
    user_id: int | None,
) -> None | models.User:
    """基于 query token + user_id 的 WebSocket 认证。"""
    if not user_token or user_id is None:
        logger.warning(
            "WebSocket connection missing auth query for room %s. token=%s, user_id=%s",
            roomid,
            bool(user_token),
            user_id,
        )
        return None

    stmt = select(models.User).where(
        models.User.id == user_id,
        models.User.token == user_token,
        models.User.room_id == roomid,
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        # 诊断：逐项检查哪个条件不匹配
        user_exists = await session.scalar(
            select(models.User.id).where(models.User.id == user_id))
        if user_exists is None:
            logger.warning(
                "Auth fail: user_id=%s does not exist in DB",
                user_id,
            )
        else:
            # 用户存在，检查 token 和 room_id
            user_row = await session.scalar(
                select(models.User).where(models.User.id == user_id))
            token_match = user_row.token == user_token if user_row else False
            room_match = user_row.room_id == roomid if user_row else False
            logger.warning(
                "Auth fail: user_id=%s, room=%s | DB: room=%s, token_match=%s, room_match=%s",
                user_id,
                roomid,
                user_row.room_id if user_row else None,
                token_match,
                room_match,
            )
        return None

    logger.info(
        "WebSocket query authentication succeeded for room %s. user_id=%s",
        roomid,
        user.id,
    )
    return user


async def authenticate_user_for_room_http(
    session: AsyncSession,
    roomid: str,
    query_params: Mapping[str, str],
    cookies: Mapping[str, str],
) -> None | models.User:
    """HTTP 房间鉴权：优先 query(token+user_id)，缺失时兼容 cookie。"""
    token = query_params.get("token")
    user_id_raw = query_params.get("user_id")

    user_id: int | None = None
    if user_id_raw is not None:
        try:
            user_id = int(user_id_raw)
        except ValueError:
            logger.warning(
                "Invalid user_id query for room %s: %s",
                roomid,
                user_id_raw,
            )
            return None

    if token is not None or user_id_raw is not None:
        # 只要出现任一 query 鉴权参数，就强制走 query 鉴权，避免 query/cookie 混用绕过。
        return await authenticate_user_by_room_token(session, roomid, token, user_id)

    return await simple_authentication(session, dict(cookies), roomid)


async def authenticate_user_global(
    session: AsyncSession,
    token: str | None,
    user_id: int | None,
) -> models.User | None:
    """全局鉴权：验证 token + user_id（不绑定房间），用于全局 CRUD 端点。"""
    if not token or user_id is None:
        return None

    stmt = select(models.User).where(
        models.User.id == user_id,
        models.User.token == token,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_room_object(session: AsyncSession, room_id: str) -> models.Room | None:
    """获取房间对象，包含用户和标签组信息。"""
    stmt = (select(models.Room).where(models.Room.id == room_id).options(
        selectinload(models.Room.users),
        selectinload(models.Room.tag_groups).selectinload(models.TagGroup.tags),
    ))

    result = await session.execute(stmt)
    return result.scalar_one_or_none()
