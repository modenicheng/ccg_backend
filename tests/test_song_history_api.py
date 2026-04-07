from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Room, Song, SongTagHistory, Tag, TagGroup, TagGroupTag, User
from db.session import get_db
from main import app


@pytest_asyncio.fixture
async def session(db_session) -> AsyncIterator[AsyncSession]:
    """使用共享的数据库会话 fixture。"""
    yield db_session


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[TestClient]:
    """Create a test client with overridden database dependency."""

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_song_tag_history_summary_aggregates_counts(
    client: TestClient,
    session: AsyncSession,
):
    """Should merge duplicate tags in the same group and expose selected_count."""
    song = Song(title="测试歌曲")
    group_1 = TagGroup(name="风格")
    group_2 = TagGroup(name="语种")
    tag_11 = Tag(name="摇滚")
    tag_12 = Tag(name="流行")
    tag_21 = Tag(name="中文")

    session.add_all([song, group_1, group_2, tag_11, tag_12, tag_21])
    await session.flush()

    session.add_all([
        TagGroupTag(group_id=group_1.id, tag_id=tag_11.id),
        TagGroupTag(group_id=group_1.id, tag_id=tag_12.id),
        TagGroupTag(group_id=group_2.id, tag_id=tag_21.id),
    ])

    session.add_all([
        SongTagHistory(song_id=song.id, tag_id=tag_11.id),
        SongTagHistory(song_id=song.id, tag_id=tag_11.id),
        SongTagHistory(song_id=song.id, tag_id=tag_11.id),
        SongTagHistory(song_id=song.id, tag_id=tag_12.id),
        SongTagHistory(song_id=song.id, tag_id=tag_21.id),
        SongTagHistory(song_id=song.id, tag_id=tag_21.id),
    ])
    await session.commit()

    response = client.get(f"/api/songs/{song.id}/history/tags")
    assert response.status_code == 200

    payload = response.json()
    assert payload["song_id"] == song.id
    assert len(payload["groups"]) == 2

    first_group = payload["groups"][0]
    assert first_group["group_id"] == group_1.id
    assert [item["tag_id"] for item in first_group["tags"]] == [tag_11.id, tag_12.id]
    assert [item["selected_count"] for item in first_group["tags"]] == [3, 1]

    second_group = payload["groups"][1]
    assert second_group["group_id"] == group_2.id
    assert len(second_group["tags"]) == 1
    assert second_group["tags"][0]["tag_id"] == tag_21.id
    assert second_group["tags"][0]["selected_count"] == 2


@pytest.mark.asyncio
async def test_get_song_tag_history_records_returns_raw_entries(
    client: TestClient,
    session: AsyncSession,
):
    """Should return detailed room/judge history records for one tag."""
    song = Song(title="详情测试歌曲")
    group_1 = TagGroup(name="情绪")
    tag = Tag(name="热血")
    room_a = Room(id="room-a", title="房间A")
    room_b = Room(id="room-b", title="房间B")
    judge_user = User(username="owner", token="token-owner", is_owner=True)

    session.add_all([song, group_1, tag, room_a, room_b, judge_user])
    await session.flush()

    session.add(TagGroupTag(group_id=group_1.id, tag_id=tag.id))

    now = datetime.now(UTC)
    older = SongTagHistory(
        song_id=song.id,
        tag_id=tag.id,
        judged_by_user_id=judge_user.id,
        room_id="room-a",
        created_at=now - timedelta(minutes=1),
    )
    newer = SongTagHistory(
        song_id=song.id,
        tag_id=tag.id,
        judged_by_user_id=judge_user.id,
        room_id="room-b",
        created_at=now,
    )

    session.add_all([older, newer])
    await session.commit()

    response = client.get(
        f"/api/songs/{song.id}/history/tags/{tag.id}/records",
        params={"group_id": group_1.id},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["song_id"] == song.id
    assert payload["tag_id"] == tag.id
    assert payload["group_id"] == group_1.id
    assert payload["total"] == 2
    assert payload["records"][0]["room_id"] == "room-b"
    assert payload["records"][0]["judged_by_username"] == "owner"
    assert payload["records"][1]["room_id"] == "room-a"


@pytest.mark.asyncio
async def test_get_song_tag_history_records_not_found(
    client: TestClient,
    session: AsyncSession,
):
    """Should return 404 when there is no matching history record."""
    song = Song(title="空历史歌曲")
    tag = Tag(name="未命中标签")
    session.add_all([song, tag])
    await session.commit()

    response = client.get(f"/api/songs/{song.id}/history/tags/{tag.id}/records")
    assert response.status_code == 404
    assert response.json()["detail"] == "No history records found for this song tag"
