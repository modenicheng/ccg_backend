"""Heartbeat-based player online status monitor.

Tracks client heartbeats via Redis timestamps and syncs online/offline status
to the database and all connected clients via WebSocket broadcast.

- Online detection: immediate, triggered by any heartbeat PING/PONG frame
- Offline detection: background task every 5s, marks offline after 15s (3 intervals)
  without receiving a heartbeat
"""

from __future__ import annotations

import asyncio
from time import time

from cache.connection import get_redis
from client_manager import ClientManager, Client
from db.crud.room_state_related import update_player_online_status
from db.session import session_scope
from schemas.ws_messages.room_schemas import (
    PlayerJoinMessage,
    PlayerLeaveMessage,
    RoomStatePlayerItem,
)
from utils import get_logger

logger = get_logger(__name__)

CHECK_INTERVAL_SECONDS = 5.0
OFFLINE_THRESHOLD_MS = 15000  # 3 * 5s
HB_KEY_TTL = 30

HB_TS_KEY_PREFIX = "hb:ts"
HB_STATUS_KEY_PREFIX = "hb:online"


class HeartbeatMonitor:
    """Background monitor that periodically checks heartbeat timestamps
    and marks players offline when their heartbeat has expired."""

    def __init__(self, clients: ClientManager):
        self._clients = clients
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background monitoring task."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())
        logger.info("Heartbeat monitor started (interval=%ss, threshold=%sms)",
                    CHECK_INTERVAL_SECONDS, OFFLINE_THRESHOLD_MS)

    async def stop(self) -> None:
        """Stop the background monitoring task."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("Heartbeat monitor stopped")

    async def _run(self) -> None:
        while True:
            try:
                await self._check()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Heartbeat monitor check iteration failed")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _check(self) -> None:
        try:
            redis = await get_redis()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Heartbeat monitor: Redis unavailable, skipping check")
            return

        snapshot = self._clients.get_room_snapshot()
        now_ms = int(time() * 1000)

        for room_id, room_clients in snapshot.items():
            for client in room_clients:
                if client.is_spectator:
                    continue
                ts_key = f"{HB_TS_KEY_PREFIX}:{room_id}:{client.user_id}"
                last_ts_raw = await redis.get(ts_key)

                if last_ts_raw is None:
                    await self._mark_offline(room_id, client, redis)
                elif now_ms - int(last_ts_raw) >= OFFLINE_THRESHOLD_MS:
                    await self._mark_offline(room_id, client, redis)

    async def _mark_offline(
        self,
        room_id: str,
        client: Client,
        redis,
    ) -> None:
        status_key = f"{HB_STATUS_KEY_PREFIX}:{room_id}:{client.user_id}"
        current_status = await redis.get(status_key)
        if current_status == "0":
            return

        await redis.set(status_key, "0", ex=HB_KEY_TTL)
        await redis.delete(f"{HB_TS_KEY_PREFIX}:{room_id}:{client.user_id}")

        try:
            async with session_scope() as session:
                await update_player_online_status(session, client.user_id, False)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Heartbeat monitor: failed to update DB offline for user %d",
                client.user_id,
            )
            return

        player_item = RoomStatePlayerItem(
            id=client.user_id,
            username=client.username,
            is_owner=client.is_owner,
            online=False,
        )
        leave_message = PlayerLeaveMessage(data=player_item)
        await self._clients.broadcast(room_id, leave_message.model_dump())
        logger.info(
            "Heartbeat monitor: player %d marked offline in room %s",
            client.user_id,
            room_id,
        )


async def mark_player_online(
    room_id: str,
    client: Client,
    clients: ClientManager,
) -> None:
    """Mark a player as online after receiving a heartbeat.

    Called from the heartbeat handler. Only performs DB update and broadcast
    on actual offline→online transitions (guarded by a Redis status key).
    """
    try:
        redis = await get_redis()
    except Exception:  # pylint: disable=broad-exception-caught
        return

    status_key = f"{HB_STATUS_KEY_PREFIX}:{room_id}:{client.user_id}"
    current_status = await redis.get(status_key)
    if current_status == "1":
        return

    await redis.set(status_key, "1", ex=HB_KEY_TTL)

    try:
        async with session_scope() as session:
            await update_player_online_status(session, client.user_id, True)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Heartbeat: failed to update DB online for user %d",
            client.user_id,
        )
        return

    player_item = RoomStatePlayerItem(
        id=client.user_id,
        username=client.username,
        is_owner=client.is_owner,
        online=True,
    )
    join_message = PlayerJoinMessage(data=player_item)
    await clients.broadcast(room_id, join_message.model_dump())
    logger.info(
        "Heartbeat: player %d marked online in room %s",
        client.user_id,
        room_id,
    )


async def init_heartbeat_state(room_id: str, user_id: int) -> None:
    """Initialize heartbeat tracking state for a newly connected client.

    Called from on_connect to ensure the monitor does not immediately flag
    a freshly connected player as offline.
    """
    try:
        redis = await get_redis()
        now_ms = int(time() * 1000)
        await redis.set(f"{HB_TS_KEY_PREFIX}:{room_id}:{user_id}",
                        now_ms,
                        ex=HB_KEY_TTL)
        await redis.set(f"{HB_STATUS_KEY_PREFIX}:{room_id}:{user_id}",
                        "1",
                        ex=HB_KEY_TTL)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Failed to init heartbeat state for user %d in room %s",
            user_id,
            room_id,
        )


async def cleanup_heartbeat_state(room_id: str, user_id: int) -> None:
    """Clean up heartbeat tracking state for a disconnecting client.

    Called from on_disconnect to prevent the monitor from trying to
    track a departed client.
    """
    try:
        redis = await get_redis()
        await redis.delete(
            f"{HB_TS_KEY_PREFIX}:{room_id}:{user_id}",
            f"{HB_STATUS_KEY_PREFIX}:{room_id}:{user_id}",
        )
    except Exception:  # pylint: disable=broad-exception-caught
        pass
