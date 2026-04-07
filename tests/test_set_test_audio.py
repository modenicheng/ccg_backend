from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from db import crud
from db.models import Room, RoomStatusORM, Song, User
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
async def test_set_test_audio_rejects_when_room_not_waiting(client: TestClient,
                                                            session: AsyncSession,
                                                            monkeypatch):
    room = Room(id="ROOMA1", title="room", status=RoomStatusORM.RUNNING)
    owner = User(username="owner1",
                 token="tok-owner-1",
                 is_owner=True,
                 room_id=room.id,
                 online=True)
    song = Song(
        title="song-1",
        platform="qq",
        platform_song_id="platform-song-1",
        cached_path="cached-1.mp3",
    )
    session.add_all([room, owner, song])
    await session.commit()

    sent: list[tuple[str, dict]] = []
    persisted: list[tuple[str, Any]] = []

    async def fake_broadcast(room_id: str, message: dict, excluded_clients=None):
        _ = excluded_clients
        sent.append((room_id, message))

    async def fake_set_room_playback_state(room_id: str, playback_state: Any):
        persisted.append((room_id, playback_state))

    monkeypatch.setattr(app.state.clients, "broadcast", fake_broadcast)
    monkeypatch.setattr("cache.room_cache.set_room_playback_state",
                        fake_set_room_playback_state)

    resp = client.post(
        f"/api/room/{room.id}/set-test-audio",
        params={
            "token": owner.token,
            "user_id": str(owner.id),
        },
        json={"song_id": song.id},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == (
        "Room is not in WAITING state; test audio modifications are not allowed")
    assert not sent
    assert not persisted


@pytest.mark.asyncio
async def test_set_test_audio_rejects_when_song_unchanged(client: TestClient,
                                                          session: AsyncSession,
                                                          monkeypatch):
    song = Song(
        title="song-2",
        platform="qq",
        platform_song_id="platform-song-2",
        cached_path="cached-2.mp3",
    )
    session.add(song)
    await session.flush()

    room = Room(id="ROOMA2",
                title="room",
                status=RoomStatusORM.WAITING,
                test_audio_song_id=song.id)
    owner = User(username="owner2",
                 token="tok-owner-2",
                 is_owner=True,
                 room_id=room.id,
                 online=True)
    session.add_all([room, owner])
    await session.commit()

    sent: list[tuple[str, dict]] = []
    persisted: list[tuple[str, Any]] = []

    async def fake_broadcast(room_id: str, message: dict, excluded_clients=None):
        _ = excluded_clients
        sent.append((room_id, message))

    async def fake_set_room_playback_state(room_id: str, playback_state: Any):
        persisted.append((room_id, playback_state))

    monkeypatch.setattr(app.state.clients, "broadcast", fake_broadcast)
    monkeypatch.setattr("cache.room_cache.set_room_playback_state",
                        fake_set_room_playback_state)

    resp = client.post(
        f"/api/room/{room.id}/set-test-audio",
        params={
            "token": owner.token,
            "user_id": str(owner.id),
        },
        json={"song_id": song.id},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Test audio song is unchanged"
    assert not sent
    assert not persisted


@pytest.mark.asyncio
async def test_set_test_audio_broadcasts_and_resets_progress(client: TestClient,
                                                             session: AsyncSession,
                                                             monkeypatch):
    old_song = Song(
        title="old-song",
        platform="qq",
        platform_song_id="platform-song-old",
        cached_path="cached-old.mp3",
    )
    new_song = Song(
        title="new-song",
        platform="qq",
        platform_song_id="platform-song-new",
        cached_path="cached-new.mp3",
    )
    session.add_all([old_song, new_song])
    await session.flush()

    room = Room(id="ROOMA3",
                title="room",
                status=RoomStatusORM.WAITING,
                test_audio_song_id=old_song.id)
    owner = User(username="owner3",
                 token="tok-owner-3",
                 is_owner=True,
                 room_id=room.id,
                 online=True)
    session.add_all([room, owner])
    await session.commit()

    sent: list[tuple[str, dict]] = []
    persisted: list[tuple[str, Any]] = []

    async def fake_broadcast(room_id: str, message: dict, excluded_clients=None):
        _ = excluded_clients
        sent.append((room_id, message))

    async def fake_set_room_playback_state(room_id: str, playback_state: Any):
        persisted.append((room_id, playback_state))

    monkeypatch.setattr(app.state.clients, "broadcast", fake_broadcast)
    monkeypatch.setattr("cache.room_cache.set_room_playback_state",
                        fake_set_room_playback_state)
    monkeypatch.setattr("router.room.os.path.exists", lambda _path: True)

    resp = client.post(
        f"/api/room/{room.id}/set-test-audio",
        params={
            "token": owner.token,
            "user_id": str(owner.id),
        },
        json={"song_id": new_song.id},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "completed"
    assert body["song_id"] == new_song.id

    assert len(sent) == 1
    room_id, message = sent[0]
    assert room_id == room.id
    assert message["event"] == 20
    assert message["data"]["audio_url"] == f"/api/songs/file/{new_song.id}"
    assert message["data"]["progress_ms"] == 0
    assert message["data"]["offset_ts"] == message["ts"]

    assert len(persisted) == 1
    persisted_room_id, playback_state = persisted[0]
    assert persisted_room_id == room.id
    assert playback_state.audio_url == f"/api/songs/file/{new_song.id}"
    assert playback_state.progress_ms == 0
    assert playback_state.current_order == -1
    assert playback_state.play_state == "playing"

    await session.refresh(room)
    assert room.test_audio_song_id == new_song.id


@pytest.mark.asyncio
async def test_set_test_audio_returns_task_status_when_download_queued(
    client: TestClient,
    session: AsyncSession,
    monkeypatch,
):
    old_song = Song(
        title="old-song-task",
        platform="qq",
        platform_song_id="platform-song-old-task",
        cached_path="cached-old-task.mp3",
    )
    new_song = Song(
        title="new-song-task",
        platform="qq",
        platform_song_id="platform-song-new-task",
        cached_path=None,
    )
    session.add_all([old_song, new_song])
    await session.flush()

    room = Room(id="ROOMA4",
                title="room",
                status=RoomStatusORM.WAITING,
                test_audio_song_id=old_song.id)
    owner = User(username="owner4",
                 token="tok-owner-4",
                 is_owner=True,
                 room_id=room.id,
                 online=True)
    session.add_all([room, owner])
    await session.commit()

    sent: list[tuple[str, dict]] = []
    persisted: list[tuple[str, Any]] = []
    enqueued: list[str] = []

    async def fake_broadcast(room_id: str, message: dict, excluded_clients=None):
        _ = excluded_clients
        sent.append((room_id, message))

    async def fake_set_room_playback_state(room_id: str, playback_state: Any):
        persisted.append((room_id, playback_state))

    def fake_download_and_cache_song(mid: str, save_path: str | None = None):
        _ = save_path
        enqueued.append(mid)
        return None

    monkeypatch.setattr(app.state.clients, "broadcast", fake_broadcast)
    monkeypatch.setattr("cache.room_cache.set_room_playback_state",
                        fake_set_room_playback_state)
    monkeypatch.setattr("router.room.tasks.download_and_cache_song",
                        fake_download_and_cache_song)

    resp = client.post(
        f"/api/room/{room.id}/set-test-audio",
        params={
            "token": owner.token,
            "user_id": str(owner.id),
        },
        json={"song_id": new_song.id},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "task"
    assert body["task_status"] == "pending"
    assert body["song_id"] == new_song.id
    assert body["task_id"] == f"download_and_cache_song:{new_song.platform_song_id}"

    assert enqueued == [new_song.platform_song_id]
    assert not sent
    assert not persisted

    await session.refresh(room)
    assert room.test_audio_song_id == old_song.id

    task_record = await crud.get_task_record_by_task_id(session, body["task_id"])
    assert task_record is not None
    assert task_record.status == "pending"
    assert task_record.task_name == "download_and_cache_song"


@pytest.mark.asyncio
async def test_set_test_audio_polling_returns_task_status_when_running(
    client: TestClient,
    session: AsyncSession,
    monkeypatch,
):
    old_song = Song(
        title="old-song-running",
        platform="qq",
        platform_song_id="platform-song-old-running",
        cached_path="cached-old-running.mp3",
    )
    new_song = Song(
        title="new-song-running",
        platform="qq",
        platform_song_id="platform-song-running",
        cached_path=None,
    )
    session.add_all([old_song, new_song])
    await session.flush()

    room = Room(id="ROOMA5",
                title="room",
                status=RoomStatusORM.WAITING,
                test_audio_song_id=old_song.id)
    owner = User(username="owner5",
                 token="tok-owner-5",
                 is_owner=True,
                 room_id=room.id,
                 online=True)
    session.add_all([room, owner])
    await crud.create_task_record(
        session=session,
        task_id=f"download_and_cache_song:{new_song.platform_song_id}",
        task_name="download_and_cache_song",
        status="running",
        result_json={"platform_song_id": new_song.platform_song_id},
    )
    await session.commit()

    sent: list[tuple[str, dict]] = []
    persisted: list[tuple[str, Any]] = []
    enqueued: list[str] = []

    async def fake_broadcast(room_id: str, message: dict, excluded_clients=None):
        _ = excluded_clients
        sent.append((room_id, message))

    async def fake_set_room_playback_state(room_id: str, playback_state: Any):
        persisted.append((room_id, playback_state))

    def fake_download_and_cache_song(mid: str, save_path: str | None = None):
        _ = save_path
        enqueued.append(mid)
        return None

    monkeypatch.setattr(app.state.clients, "broadcast", fake_broadcast)
    monkeypatch.setattr("cache.room_cache.set_room_playback_state",
                        fake_set_room_playback_state)
    monkeypatch.setattr("router.room.tasks.download_and_cache_song",
                        fake_download_and_cache_song)

    resp = client.post(
        f"/api/room/{room.id}/set-test-audio",
        params={
            "token": owner.token,
            "user_id": str(owner.id),
        },
        json={"song_id": new_song.id},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "task"
    assert body["task_status"] == "running"
    assert body["task_id"] == f"download_and_cache_song:{new_song.platform_song_id}"

    assert not enqueued
    assert not sent
    assert not persisted


@pytest.mark.asyncio
async def test_set_test_audio_returns_completed_when_task_success_and_file_ready(
    client: TestClient,
    session: AsyncSession,
    tmp_path,
    monkeypatch,
):
    old_song = Song(
        title="old-song-complete",
        platform="qq",
        platform_song_id="platform-song-old-complete",
        cached_path="cached-old-complete.mp3",
    )
    cached_file = tmp_path / "cached-new-complete.ogg"
    cached_file.write_bytes(b"ok")
    new_song = Song(
        title="new-song-complete",
        platform="qq",
        platform_song_id="platform-song-complete",
        cached_path=str(cached_file),
    )
    session.add_all([old_song, new_song])
    await session.flush()

    room = Room(id="ROOMA6",
                title="room",
                status=RoomStatusORM.WAITING,
                test_audio_song_id=old_song.id)
    owner = User(username="owner6",
                 token="tok-owner-6",
                 is_owner=True,
                 room_id=room.id,
                 online=True)
    session.add_all([room, owner])
    await crud.create_task_record(
        session=session,
        task_id=f"download_and_cache_song:{new_song.platform_song_id}",
        task_name="download_and_cache_song",
        status="success",
        result_json={"cached_path": str(cached_file)},
    )
    await session.commit()

    sent: list[tuple[str, dict]] = []
    persisted: list[tuple[str, Any]] = []
    enqueued: list[str] = []

    async def fake_broadcast(room_id: str, message: dict, excluded_clients=None):
        _ = excluded_clients
        sent.append((room_id, message))

    async def fake_set_room_playback_state(room_id: str, playback_state: Any):
        persisted.append((room_id, playback_state))

    def fake_download_and_cache_song(mid: str, save_path: str | None = None):
        _ = save_path
        enqueued.append(mid)
        return None

    monkeypatch.setattr(app.state.clients, "broadcast", fake_broadcast)
    monkeypatch.setattr("cache.room_cache.set_room_playback_state",
                        fake_set_room_playback_state)
    monkeypatch.setattr("router.room.tasks.download_and_cache_song",
                        fake_download_and_cache_song)

    resp = client.post(
        f"/api/room/{room.id}/set-test-audio",
        params={
            "token": owner.token,
            "user_id": str(owner.id),
        },
        json={"song_id": new_song.id},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "completed"
    assert body["song_id"] == new_song.id
    assert body["task_id"] == f"download_and_cache_song:{new_song.platform_song_id}"

    assert not enqueued
    assert len(sent) == 1
    assert len(persisted) == 1

    await session.refresh(room)
    assert room.test_audio_song_id == new_song.id
