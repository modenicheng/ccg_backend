"""CRUD helpers for room state management (SQL-backed)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Room, TagGroup, User
from utils import get_logger

logger = get_logger(__name__)


async def get_room_with_state(
    session: AsyncSession,
    room_id: str,
) -> Room | None:
    """Load a Room with tag_groups→tags populated, for building RoomBaseStateCache."""
    stmt = (select(Room).where(Room.id == room_id).options(
        selectinload(Room.tag_groups).selectinload(TagGroup.tags)))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_room_players(
    session: AsyncSession,
    room_id: str,
) -> list[User]:
    """Return all User rows that belong to the given room."""
    stmt = select(User).where(User.room_id == room_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_player_by_id(
    session: AsyncSession,
    player_id: int,
) -> User | None:
    """Return a User by primary key."""
    return await session.get(User, player_id)


async def update_player_online_status(
    session: AsyncSession,
    player_id: int,
    online: bool,
) -> bool:
    """Set users.online for the given player.

    Returns True if the user was found and updated, False otherwise.
    """
    user = await session.get(User, player_id)
    if user is None:
        logger.warning("update_player_online_status: user %d not found", player_id)
        return False
    user.online = online
    return True


async def set_all_room_players_offline(
    session: AsyncSession,
    room_id: str,
) -> int:
    """Set online=False for all users in a room.

    Returns the number of users updated.
    """
    stmt = select(User).where(User.room_id == room_id)
    result = await session.execute(stmt)
    users = result.scalars().all()
    count = 0
    for user in users:
        if user.online:
            user.online = False
            count += 1
    return count


async def set_room_start_position(
    session: AsyncSession,
    room_id: str,
    percent: float,
) -> bool:
    """Update rooms.song_start_range_percent.

    Returns True if the room was found and updated, False otherwise.
    """
    room = await session.get(Room, room_id)
    if room is None:
        logger.warning("set_room_start_position: room %s not found", room_id)
        return False
    room.song_start_range_percent = percent
    return True


async def get_room_playback_state_json(
    session: AsyncSession,
    room_id: str,
) -> dict[str, Any] | None:
    """Return the persisted playback state JSON for a room, or None if absent."""
    room = await session.get(Room, room_id)
    if room is None:
        return None
    return room.playback_state_json  # type: ignore[return-value]


async def set_room_playback_state_json(
    session: AsyncSession,
    room_id: str,
    state_dict: dict[str, Any],
) -> bool:
    """Persist the playback state JSON for a room.

    Returns True if the room was found and updated, False otherwise.
    """
    room = await session.get(Room, room_id)
    if room is None:
        logger.warning("set_room_playback_state_json: room %s not found", room_id)
        return False
    room.playback_state_json = state_dict
    return True


async def get_room_current_song_index(
    session: AsyncSession,
    room_id: str,
) -> int | None:
    """Return rooms.current_song_index for the given room."""
    room = await session.get(Room, room_id)
    if room is None:
        return None
    return room.current_song_index
