import logging
from client_manager import ClientManager
from utils import get_event_type, get_logger, init_logging
from utils.dataframe import HeartbeatFrame
from utils.enumerations import EventType
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from handlers import handle
import asyncio
import datetime
init_logging(level=logging.DEBUG)
logger = get_logger(__name__)


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
)

clients = ClientManager()

async def heartbeat_init(ws: WebSocket):
    while True:
        hb = HeartbeatFrame()
        logger.debug("Sending heartbeat: uid=%s ts=%s", hb.uid, hb.timestamp)
        if ws.client_state.value != 1:  # WebSocketState.CONNECTED
            logger.warning("WebSocket is not connected, stopping heartbeat: %s", ws.client)
            break
        await ws.send_bytes(hb.bin)
        del hb
        await asyncio.sleep(10)

@app.websocket("/ws/")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connected: %s", websocket.client)
    clients.push(websocket)
    asyncio.create_task(heartbeat_init(websocket))
    while True:
        try:
            data = await websocket.receive_bytes()
        except KeyError:
            logger.warning(f"WebSocket disconnected: {websocket.client}. The data is not in bytes format.")
            break
        event: EventType = get_event_type(data)
        logger.debug("Received event: %s", event.name)
        try:
            await handle(event, data, clients, websocket)
        except Exception as e:
            logger.warning(f"Failed to handle event: {event.name}, error: {e}")
            await clients.send(websocket, f"Failed to handle event: {event.name}, error: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
