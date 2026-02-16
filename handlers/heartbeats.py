from utils import enumerations, get_logger
from utils.dataframe import HeartbeatFrame
from . import regist

logger = get_logger(__name__)

@regist(enumerations.EventType.HEARTBEAT)
async def handle_heartbeat(data: bytes, clients=None, websocket=None):
    frame = HeartbeatFrame.load(data)
    logger.debug("Heartbeat received: uid=%s ts=%s", frame.uid, frame.timestamp)