from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Room, Tag, TagGroup, User
from db.session import get_db
from main import app


@pytest_asyncio.fixture
async def session(db_session) -> AsyncIterator[AsyncSession]:
    yield db_session


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[TestClient]:

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_room_setting_broadcasts_tag_group_event(client: TestClient,
                                                       session: AsyncSession,
                                                       monkeypatch):
    room = Room(id="ROOM01", title="t1")
    owner = User(username="owner",
                 token="tok-owner",
                 is_owner=True,
                 room_id=room.id,
                 online=True)
    tag = Tag(name="摇滚")
    group = TagGroup(name="风格", tags=[tag])

    session.add_all([room, owner, tag, group])
    await session.commit()

    sent: list[tuple[str, dict]] = []

    async def fake_broadcast(room_id: str, message: dict, excluded_clients=None):
        _ = excluded_clients
        sent.append((room_id, message))

    monkeypatch.setattr(app.state.clients, "broadcast", fake_broadcast)

    resp = client.patch(f"/api/room/{room.id}", json={"tag_group_ids": [group.id]})

    assert resp.status_code == 200
    assert sent, "expected TAG_GROUP broadcast when room selected taggroups changed"

    room_id, message = sent[-1]
    assert room_id == room.id
    assert message["event"] == 62
    assert message["event"] != 12
    assert message["data"]["room_id"] == room.id
    assert [g["id"] for g in message["data"]["tag_groups"]] == [group.id]
