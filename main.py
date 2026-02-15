import logging

from utils import get_event_type, get_logger, init_logging
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

async def heartbeat_init():
    while True:
        logger.info("Heartbeat check: %s", datetime.datetime.now())
        await asyncio.sleep(10)

@app.websocket("/ws/")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connected: %s", websocket.client)
    while True:
        try:
            data = await websocket.receive_bytes()
        except KeyError:
            logger.warning(f"WebSocket disconnected: {websocket.client}. The data is not in bytes format.")
            break
        event: EventType = get_event_type(data)
        logger.debug("Received event: %s", event.name)
        try:
            await handle(event, data)
        except ValueError:
            logger.warning(f"Failed to handle event: {event.name}")
            await websocket.send_text(f"Failed to handle event: {event.name}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3200)
