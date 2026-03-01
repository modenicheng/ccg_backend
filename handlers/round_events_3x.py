from client_manager import ClientManager, Client
from db.session import session_scope
from utils import get_logger
from utils.enumerations import GameEventType
from db.models import RoomStatusORM
from . import regist
from schemas.ws_messages.round_event_schemas import *
from sqlalchemy import select
from db import models
import asyncio

logger = get_logger(__name__)


@regist(GameEventType.GAME_START, data_validator=GameStartMessage)
async def handle_game_start(
    data: GameStartMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
):
    logger.info(
        f"Handling game start event for room {room_id} with data: {data}")

    async with session_scope() as session:
        room = (await session.execute(
            select(models.Room).where(models.Room.id == room_id)
        )).scalar_one_or_none()

        if not room:
            logger.error(f"Room {room_id} not found")
            await client.send_error(GameEventType.GAME_START, "Room not found")
            return

        if room.status != RoomStatusORM.WAITING:
            logger.warning(
                f"Received game start event for room {room_id} which is not in waiting state"
            )
            await client.send_error(GameEventType.GAME_START,
                                    "Room is not in waiting state")
            return
        room.status = RoomStatusORM.RUNNING
        await session.commit()
        await clients.broadcast(room_id, data.model_dump())

@regist(GameEventType.ROUND_END, data_validator=RoundEndMessage)
async def handle_round_end(
    data: RoundEndMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
):
    logger.info(
        f"Handling round end event for room {room_id} with data: {data}")
    if not client.user.is_owner:
        logger.warning(
            f"User {client.user.username} attempted to end round in room {room_id} but is not the owner"
        )
        await client.send_error(GameEventType.ROUND_END,
                                "Only the room owner can end the round manually")
        return
    
    await clients.broadcast(room_id, data.model_dump())