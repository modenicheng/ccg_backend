from datetime import datetime
from fastapi import WebSocket

from utils import enumerations, get_logger
from utils.dataframe import HeartbeatFrame
from . import regist

logger = get_logger(__name__)

@regist(enumerations.EventType.HEARTBEAT)
async def handle_heartbeat(data: bytes, clients: set[WebSocket] | None = None, websocket: WebSocket | None = None):
    server_recv_ts = int(datetime.now().timestamp() * 1000)
    frame = HeartbeatFrame.load(data)
    logger.debug(
        "Heartbeat received: uid=%s ts=%s type=%s",
        frame.uid,
        frame.timestamp,
        frame.heartbeat_type.name,
    )
    if not websocket:
        logger.warning("No websocket provided for heartbeat response, skipping.")
        return

    
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

    logger.debug("Heartbeat pong received: t1=%s t2=%s t3=%s t4=%s", frame.t1, frame.t2, frame.t3, frame.t4)