"""SQL-backed compatibility APIs for room/player state.

These functions previously lived in cache.room_cache but are SQL operations.
They are moved under db/crud to keep cache module Redis-focused.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from cache.schemas import RoomBaseStateCache, RoomStatePlayerItem, RoomStateTagGroupItem
from db.models import Room, RoomStatusORM
from utils import get_logger

from .room_state_related import (
    get_player_by_id,
    get_room_players as _get_room_players_rows,
    get_room_with_state,
    update_player_online_status,
)

logger = get_logger(__name__)


async def load_room_state(
    room_id: str,
    session: AsyncSession,
) -> RoomBaseStateCache | None:
    """Load room base state from SQL and map to RoomBaseStateCache."""

    async def _load(db: AsyncSession) -> RoomBaseStateCache | None:
        room = await get_room_with_state(db, room_id)
        if room is None:
            logger.debug("No room found for room %s", room_id)
            return None
        return RoomBaseStateCache(
            room_id=room.id,
            title=room.title,
            status=RoomStatusORM(room.status),
            song_start_range_percent=room.song_start_range_percent or 0.0,
            tag_groups=[
                RoomStateTagGroupItem.model_validate(tg)
                for tg in (room.tag_groups or [])
                if tg.tags
            ],
        )

    try:
        return await _load(session)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error loading room state for room %s: %s", room_id, e, exc_info=True)
        return None


async def save_room_state(
    room_id: str,
    state: RoomBaseStateCache,
    session: AsyncSession,
) -> None:
    """Persist room base state to SQL."""

    async def _save(db: AsyncSession) -> None:
        room = await db.get(Room, room_id)
        if room is None:
            logger.warning("save_room_state: room %s not found", room_id)
            return
        room.song_start_range_percent = state.song_start_range_percent
        if state.title is not None:
            room.title = state.title

    try:
        await _save(session)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error saving room state for room %s: %s", room_id, e, exc_info=True)


async def get_room_players(
    room_id: str,
    session: AsyncSession,
) -> list[RoomStatePlayerItem]:
    """Get room players from SQL as RoomStatePlayerItem list."""

    async def _fetch(db: AsyncSession) -> list[RoomStatePlayerItem]:
        users = await _get_room_players_rows(db, room_id)
        return [RoomStatePlayerItem.model_validate(u) for u in users]

    try:
        return await _fetch(session)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error getting room players for room %s: %s", room_id, e, exc_info=True)
        return []


async def set_room_player(
    room_id: str,
    player: RoomStatePlayerItem,
    session: AsyncSession,
) -> bool:
    """Set player online status in SQL (idempotent write)."""

    async def _set(db: AsyncSession) -> bool:
        return await update_player_online_status(db, player.id, player.online)

    try:
        return await _set(session)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error setting room player %s in room %s: %s", player.id, room_id, e, exc_info=True)
        return False


async def get_room_player(
    room_id: str,
    player_id: int,
    session: AsyncSession,
) -> RoomStatePlayerItem | None:
    """Get one room player from SQL as RoomStatePlayerItem."""

    async def _get(db: AsyncSession) -> RoomStatePlayerItem | None:
        user = await get_player_by_id(db, player_id)
        if user is None or user.room_id != room_id:
            return None
        return RoomStatePlayerItem.model_validate(user)

    try:
        return await _get(session)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error getting player %s in room %s: %s", player_id, room_id, e, exc_info=True)
        return None


async def update_room_player_online_status(
    room_id: str,
    player_id: int,
    online: bool,
    session: AsyncSession,
) -> bool:
    """Update player online status in SQL."""

    async def _update(db: AsyncSession) -> bool:
        return await update_player_online_status(db, player_id, online)

    try:
        return await _update(session)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(
            "Error updating player online status %s in room %s: %s",
            player_id,
            room_id,
            e,
            exc_info=True,
        )
        return False


async def remove_room_player(
    room_id: str,
    player_id: int,
    session: AsyncSession,
) -> None:
    """Mark room player offline in SQL (do not delete row)."""
    await update_room_player_online_status(room_id, player_id, False, session)
