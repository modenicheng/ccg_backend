from db.models import Room
from utils import get_logger
from schemas import CreateRoomResponse, PatchRoomRequest, RoomInfoResponse, CreateRoomRequest
from fastapi import APIRouter, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, Depends
import json
import secrets
import string
from cache.connection import redis_client
from cache.utils import RedisKeys, room_manager, session_manager
from uuid import uuid4
from db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

room_router = APIRouter(prefix="/api/room", tags=["room"])


def generate_room_id(length: int = 6) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


async def get_room_info_payload(roomid: str) -> RoomInfoResponse:
    redis = await redis_client.get_client()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    room_key = RedisKeys.room(roomid)
    room_data = await redis.hgetall(room_key)
    if not room_data:
        raise HTTPException(status_code=404, detail="Room not found")

    players = sorted(list(await room_manager.get_room_players(roomid)))
    song_queue = await room_manager.get_song_queue(roomid)

    tag_groups_raw = room_data.get("tag_groups", "{}")
    try:
        tag_groups = json.loads(tag_groups_raw) if tag_groups_raw else {}
    except json.JSONDecodeError:
        tag_groups = {}

    play_progress = int(room_data.get("play_progress", 0) or 0)

    return RoomInfoResponse(
        roomId=roomid,
        hostPlayerId=room_data.get("host_player_id", ""),
        status=room_data.get("status", "waiting"),
        title=room_data.get("title"),
        description=room_data.get("description"),
        players=players,
        tagGroups=tag_groups,
        playProgress=play_progress,
    )


@room_router.post("/", response_model=CreateRoomResponse)
async def create_room(
    info: CreateRoomRequest,
    session: AsyncSession = Depends(get_db)
) -> CreateRoomResponse:
    room_id = generate_room_id()
    logger.info(f"Creating room with ID: {room_id}")

    new_room = Room(
        id=room_id,
        title=info.title,
    )
    session.add(new_room)
    
    redis = await redis_client.get_client()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    await session.commit()

    return CreateRoomResponse(room_id=room_id, host_name=info.host_name, host_token=str(uuid4()))


@room_router.get("/{roomid}", response_model=RoomInfoResponse)
async def room_info(roomid: str) -> RoomInfoResponse:
    return await get_room_info_payload(roomid)


@room_router.patch("/{roomid}", response_model=RoomInfoResponse)
async def room_setting(roomid: str,
                       payload: PatchRoomRequest) -> RoomInfoResponse:
    redis = await redis_client.get_client()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    room_key = RedisKeys.room(roomid)
    room_data = await redis.hgetall(room_key)
    if not room_data:
        raise HTTPException(status_code=404, detail="Room not found")

    updates: dict[str, str | int] = {}

    if payload.songQueue is not None:
        await room_manager.set_song_queue(roomid, payload.songQueue)

    if payload.title is not None:
        updates["title"] = payload.title

    if payload.description is not None:
        updates["description"] = payload.description

    if payload.tagGroups is not None:
        updates["tag_groups"] = json.dumps(payload.tagGroups,
                                           ensure_ascii=False)

    if updates:
        await redis.hset(room_key, mapping=updates)

    return await get_room_info_payload(roomid)
