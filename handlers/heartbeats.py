"""WebSocket heartbeat event handler."""
from __future__ import annotations
from datetime import datetime
from fastapi import WebSocket

from client_manager import ClientManager, Client
from utils import enumerations, get_logger
from utils.dataframe import HeartbeatFrame
from .registe_manager import regist

logger = get_logger(__name__)


@regist(enumerations.EventType.HEARTBEAT)
async def handle_heartbeat(data: bytes,
                           clients: ClientManager,
                           client: Client,
                           room_id: str | None = None) -> None:
    """Handle HEARTBEAT events (ping/pong)."""
    # pylint: disable=unused-argument
    server_recv_ts = int(datetime.now().timestamp() * 1000)
    frame: HeartbeatFrame = HeartbeatFrame.load(data)
    logger.debug(
        "Heartbeat received: uid=%s ts=%s type=%s",
        frame.uid,
        frame.timestamp,
        frame.heartbeat_type.name,
    )
    websocket: WebSocket = client.ws

    if frame.heartbeat_type == enumerations.HeartbeatType.PING:
        logger.debug("Heartbeat ping received: t1=%s t2=%s", frame.t1, server_recv_ts)
        response = HeartbeatFrame(
            heartbeat_type=enumerations.HeartbeatType.PONG,
            uid=frame.uid,
            t1=frame.t1,
            t2=server_recv_ts,
            t3=int(datetime.now().timestamp() * 1000),
        )
        await websocket.send_bytes(response.bin)
        return

    logger.debug(
        "Heartbeat pong received: t1=%s t2=%s t3=%s t4=%s",
        frame.t1,
        frame.t2,
        frame.t3,
        frame.t4,
    )
