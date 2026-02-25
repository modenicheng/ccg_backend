#!/usr/bin/env python3
"""Test many-to-many relationships between Room and TagGroup."""
import asyncio
import sys
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from db.models import Base, Room, TagGroup

async def main():
    # Use in-memory SQLite
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        # Create a room and a tag group
        room = Room(id="test-room-1", status=0)
        tag_group = TagGroup(name="Test Group", description="Test")
        session.add_all([room, tag_group])
        await session.flush()  # to get IDs

        # Associate them via many-to-many
        room.tag_group.append(tag_group)
        await session.commit()

        # Query room's tag groups
        room_result = await session.get(Room, room.id)
        print(f"Room tag groups: {room_result.tag_group}")
        assert len(room_result.tag_group) == 1
        assert room_result.tag_group[0].id == tag_group.id

        # Query tag group's rooms
        tag_group_result = await session.get(TagGroup, tag_group.id)
        print(f"TagGroup rooms: {tag_group_result.rooms}")
        assert len(tag_group_result.rooms) == 1
        assert tag_group_result.rooms[0].id == room.id

        # Verify join table entries (optional)
        from db.models import TagGroupRoom
        stmt = select(TagGroupRoom)
        result = await session.execute(stmt)
        joins = result.scalars().all()
        print(f"Join table entries: {joins}")
        assert len(joins) == 1
        assert joins[0].group_id == tag_group.id
        assert joins[0].room_id == room.id

        print("All tests passed!")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())