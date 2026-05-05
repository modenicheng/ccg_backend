"""WebSocket heartbeat event handler.

On any heartbeat frame (PING or PONG), updates the Redis heartbeat timestamp
and marks the player online if they were previously offline.
"""
from __future__ import annotations
from datetime import datetime
from fastapi import WebSocket

from cache.connection import get_redis
from client_manager import ClientManager, Client
from utils import enumerations, get_logger
from utils.dataframe import HeartbeatFrame
from utils.heartbeat_monitor import mark_player_online
from .registe_manager import regist

logger = get_logger(__name__)

HB_TS_KEY_PREFIX = "hb:ts"
HB_KEY_TTL = 30


@regist(enumerations.EventType.HEARTBEAT)
async def handle_heartbeat(data: bytes,
                           clients: ClientManager,
                           client: Client,
                           room_id: str | None = None) -> None:
    """Handle HEARTBEAT events (ping/pong).

    Updates the Redis heartbeat timestamp for online status tracking
    and marks the player online if they were previously offline.
    """
    server_recv_ts = int(datetime.now().timestamp() * 1000)
    frame: HeartbeatFrame = HeartbeatFrame.load(data)
    websocket: WebSocket = client.ws

    try:
        redis = await get_redis()
        ts_key = f"{HB_TS_KEY_PREFIX}:{room_id}:{client.user_id}"
        await redis.set(ts_key, server_recv_ts, ex=HB_KEY_TTL)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Heartbeat: failed to update Redis timestamp for user %d",
                         client.user_id)

    try:
        await mark_player_online(room_id, client, clients)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Heartbeat: failed to mark player %d online", client.user_id)

    if frame.heartbeat_type == enumerations.HeartbeatType.PING:
        response = HeartbeatFrame(
            heartbeat_type=enumerations.HeartbeatType.PONG,
            uid=frame.uid,
            t1=frame.t1,
            t2=server_recv_ts,
            t3=int(datetime.now().timestamp() * 1000),
        )
        await websocket.send_bytes(response.bin)
