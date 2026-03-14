"""Room-related API endpoints for CCG backend."""

from __future__ import annotations
import secrets
import string
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.models import Room, User, TagGroup, RoomStatusORM
from db.session import get_db
from cache.connection import get_redis
from schemas.room import JoinRoomRequest, JoinRoomResponse
from schemas.user import UserLogin, BaseUser
from schemas.tag import TagGroupResponse
from schemas import (
    CreateRoomResponse,
    PatchRoomRequest,
    RoomInfoResponse,
    CreateRoomRequest,
)
from utils import get_logger

logger = get_logger(__name__)

room_router = APIRouter(prefix="/api/room", tags=["room"])


def _to_room_info_response(room: Room) -> RoomInfoResponse:
    host_user = next((user for user in room.users if user.is_owner), None)
    return RoomInfoResponse(
        room_id=room.id,
        host_player_id=str(host_user.id) if host_user else "",
        status=int(room.status),
        title=room.title,
        players=[BaseUser.model_validate(user) for user in room.users],
        tag_groups=[
            TagGroupResponse.model_validate(group) for group in room.tag_groups
        ],
    )


def generate_room_id(length: int = 6) -> str:
    """Generate a random room ID."""
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


@room_router.post("/", response_model=CreateRoomResponse)
async def create_room(
    info: CreateRoomRequest,
    session: AsyncSession = Depends(get_db)) -> CreateRoomResponse:
    """Create a new room."""
    room_id = generate_room_id()
    logger.info("Creating room with ID: %s", room_id)

    owner = User(username=info.host_name,
                 is_owner=True,
                 token=str(uuid4()),
                 room_id=room_id)
    session.add(owner)

    new_room = Room(id=room_id, title=info.title, users=[owner])
    session.add(new_room)

    try:
        await get_redis()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc
    await session.commit()

    return CreateRoomResponse(
        room_id=room_id,
        host=UserLogin.model_validate(owner),
    )


@room_router.post("/{roomid}", response_model=JoinRoomResponse)
async def join_room(
    roomid: str, data: JoinRoomRequest,
    session: AsyncSession = Depends(get_db)) -> JoinRoomResponse:
    """Join an existing room."""
    stmt = select(Room).where(Room.id == roomid)
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    # 如果房间已经在游戏中，拒绝加入
    if room.status != RoomStatusORM.WAITING:
        raise HTTPException(status_code=400,
                            detail="Cannot join a room that is not in waiting state")

    new_user = User(username=data.username,
                    is_owner=False,
                    token=str(uuid4()),
                    room=room)
    try:
        session.add(new_user)
        await session.commit()
    except IntegrityError as exc:
        raise HTTPException(status_code=400,
                            detail="Username already taken in this room") from exc
    return JoinRoomResponse(
        room_id=roomid,
        user=UserLogin.model_validate(new_user),
    )


@room_router.get("/{roomid}", response_model=RoomInfoResponse)
async def room_info(
    roomid: str, session: AsyncSession = Depends(get_db)) -> RoomInfoResponse:
    """Get room information."""
    stmt = (select(Room).where(Room.id == roomid).options(
        selectinload(Room.users),
        selectinload(Room.tag_groups).selectinload(TagGroup.tags),
    ))
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return _to_room_info_response(room)


@room_router.patch("/{roomid}", response_model=RoomInfoResponse)
async def room_setting(
    roomid: str, payload: PatchRoomRequest,
    session: AsyncSession = Depends(get_db)) -> RoomInfoResponse:
    """Update room settings."""
    stmt = (select(Room).where(Room.id == roomid).options(
        selectinload(Room.users),
        selectinload(Room.tag_groups).selectinload(TagGroup.tags),
    ))
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if payload.song_queue is not None:
        # await room_cache.set_room_song_queue(roomid, payload.song_queue)
        # 先直接存数据库
        pass

    if payload.title is not None:
        room.title = payload.title

    if payload.tag_group_ids is not None or payload.tag_groups is not None:
        group_ids = payload.tag_group_ids
        if group_ids is None:
            group_ids = [group.id for group in (payload.tag_groups or [])]
        group_ids = list(dict.fromkeys(group_ids))
        if group_ids:
            group_result = await session.execute(
                select(TagGroup).where(TagGroup.id.in_(group_ids)).options(
                    selectinload(TagGroup.tags)))
            found_groups = list(group_result.scalars().all())
            found_ids = {group.id for group in found_groups}
            missing_ids = [
                group_id for group_id in group_ids if group_id not in found_ids
            ]
            if missing_ids:
                raise HTTPException(
                    status_code=404,
                    detail=f"Tag groups with IDs {missing_ids} not found",
                )
            room.tag_groups = found_groups
        else:
            room.tag_groups = []

    await session.commit()

    refreshed_result = await session.execute(stmt)
    refreshed_room = refreshed_result.scalar_one_or_none()
    if not refreshed_room:
        raise HTTPException(status_code=404, detail="Room not found")
    return _to_room_info_response(refreshed_room)
