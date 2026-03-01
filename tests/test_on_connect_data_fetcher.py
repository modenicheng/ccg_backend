from handlers.game_events import on_connect
import pytest
from db.session import session_scope

from sqlalchemy.orm import selectinload

from schemas.base_message import *
from db import models
from sqlalchemy import select
from schemas.ws_messages import room_schemas as RoomSchema


@pytest.mark.asyncio
async def test_on_connect_data_fetcher():
    room_id = "KDVVYR"
    async with session_scope() as session:
        stmt = select(models.Room).where(models.Room.id == room_id).options(
            selectinload(models.Room.users),
            selectinload(models.Room.tag_groups).selectinload(
                models.TagGroup.tags))
        result = await session.execute(stmt)
        room = result.scalar_one_or_none()
        
        if not room:
             pytest.skip(f"Room with id {room_id} not found in database.")

        # 连接时发送当前播放状态
        message = RoomSchema.ClientRoomState.model_validate(room)
        assert message.room_id == room_id
        assert len(message.players) == len(room.users)
        print(message.model_dump())
