from __future__ import annotations

import datetime
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db import crud, models
from db.crud import audio_preload_and_token as audio_preload_token_crud
from router import room_songs as room_songs_router


@pytest_asyncio.fixture
async def session(db_session) -> AsyncIterator[AsyncSession]:
    """使用共享的数据库会话fixture"""
    yield db_session


@pytest.mark.asyncio
async def test_create_or_update_songlist_updates_existing_row(
    session: AsyncSession,) -> None:
    songlist = await crud.create_or_update_songlist(
        session,
        platform="qq",
        platform_songlist_id=1,
        title="old",
        creator_name="alice",
    )
    await session.commit()

    updated = await crud.create_or_update_songlist(
        session,
        platform="qq",
        platform_songlist_id=1,
        title="new",
        creator_name="bob",
    )
    await session.commit()

    assert updated.id == songlist.id
    assert updated.title == "new"
    assert updated.creator_name == "bob"


@pytest.mark.asyncio
async def test_create_or_update_song_existing_song_adds_songlist_link(
    session: AsyncSession,) -> None:
    song = await crud.create_or_update_song(
        session,
        songlist_id=None,
        platform="qq",
        platform_song_id="song-1",
        title="first",
    )
    await session.commit()

    songlist = await crud.create_or_update_songlist(
        session,
        platform="qq",
        platform_songlist_id=2,
        title="list",
    )
    await session.commit()

    updated = await crud.create_or_update_song(
        session,
        songlist_id=songlist.id,
        platform="qq",
        platform_song_id="song-1",
        title="updated",
    )
    await session.commit()

    links = (await session.execute(select(models.SonglistSong))).scalars().all()

    assert updated.id == song.id
    assert updated.title == "updated"
    assert len(links) == 1
    assert links[0].songlist_id == songlist.id
    assert links[0].song_id == song.id


@pytest.mark.asyncio
async def test_create_or_update_song_does_not_duplicate_songlist_link(
    session: AsyncSession,) -> None:
    songlist = await crud.create_or_update_songlist(
        session,
        platform="qq",
        platform_songlist_id=3,
        title="list",
    )
    await session.commit()

    song = await crud.create_or_update_song(
        session,
        songlist_id=songlist.id,
        platform="qq",
        platform_song_id="song-2",
        title="title",
    )
    await session.commit()

    _ = await crud.create_or_update_song(
        session,
        songlist_id=songlist.id,
        platform="qq",
        platform_song_id="song-2",
        title="title-2",
    )
    await session.commit()

    links = (await session.execute(select(models.SonglistSong))).scalars().all()

    assert song.id is not None
    assert len(links) == 1
    assert links[0].songlist_id == songlist.id
    assert links[0].song_id == song.id


@pytest.mark.asyncio
async def test_create_or_update_songs_bulk_upserts(session: AsyncSession) -> None:
    songlist = await crud.create_or_update_songlist(
        session,
        platform="qq",
        platform_songlist_id=114514,
        title="bulk-list",
    )
    await session.commit()

    created = await crud.create_or_update_songs(
        session,
        songlist_id=songlist.id,
        songs=[
            {
                "platform": "qq",
                "platform_song_id": "bulk-song-1",
                "title": "song-1"
            },
            {
                "platform": "qq",
                "platform_song_id": "bulk-song-2",
                "title": "song-2"
            },
        ],
    )
    await session.commit()

    assert len(created) == 2

    updated = await crud.create_or_update_songs(
        session,
        songlist_id=songlist.id,
        songs=[
            {
                "platform": "qq",
                "platform_song_id": "bulk-song-1",
                "title": "song-1-updated",
            },
            {
                "platform": "qq",
                "platform_song_id": "bulk-song-3",
                "title": "song-3"
            },
        ],
    )
    await session.commit()

    songs = ((await session.execute(
        select(models.Song).order_by(models.Song.platform_song_id))).scalars().all())
    links = (await session.execute(select(models.SonglistSong))).scalars().all()

    assert len(updated) == 2
    assert len(songs) == 3
    assert songs[0].platform_song_id == "bulk-song-1"
    assert songs[0].title == "song-1-updated"
    assert len(links) == 3


@pytest.mark.asyncio
async def test_create_or_update_songs_accepts_qq_song_shape(
    session: AsyncSession,) -> None:
    songlist = await crud.create_or_update_songlist(
        session,
        platform="qq",
        platform_songlist_id=114515,
        title="bulk-list",
    )
    await session.commit()

    songs = await crud.create_or_update_songs(
        session,
        songlist_id=songlist.id,
        songs=[{
            "songmid": "qq-mid-1",
            "songname": "qq-song",
            "singer": [{
                "name": "A"
            }, {
                "name": "B"
            }],
            "album": {
                "name": "album-1"
            },
        }],
    )
    await session.commit()

    assert len(songs) == 1
    assert songs[0].platform == "qq"
    assert songs[0].platform_song_id == "qq-mid-1"
    assert songs[0].artist == "A/B"
    assert songs[0].album_name == "album-1"


@pytest.mark.asyncio
async def test_update_song_cached_path_only_updates_cache_field(
    session: AsyncSession,) -> None:
    created = await crud.create_or_update_song(
        session,
        songlist_id=None,
        platform="qq",
        platform_song_id="cache-mid-1",
        title="cache-title",
        artist="cache-artist",
    )
    await session.commit()

    updated = await crud.update_song_cached_path(
        session,
        platform="qq",
        platform_song_id="cache-mid-1",
        cached_path="assets/audio/cache-mid-1.ogg",
    )
    await session.commit()

    assert updated is not None
    assert updated.id == created.id
    assert updated.title == "cache-title"
    assert updated.artist == "cache-artist"
    assert updated.cached_path == "assets/audio/cache-mid-1.ogg"


@pytest.mark.asyncio
async def test_prepare_preload_songs_updates_room_song_temp_url(
        session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    room = models.Room(id="ROOM01", title="room")
    song = models.Song(
        platform="qq",
        platform_song_id="qq-mid-preload",
        title="preload-song",
    )
    session.add_all([room, song])
    await session.flush()

    room_song = models.RoomSong(
        room_id=room.id,
        song_id=song.id,
        song_order=1,
    )
    session.add(room_song)
    await session.commit()

    async def _fake_generate_audio_token(song_id: int) -> str:
        return f"token-{song_id}"

    monkeypatch.setattr(audio_preload_token_crud, "generate_audio_token",
                        _fake_generate_audio_token)

    preload_song_ids = await crud.prepare_preload_songs(
        session,
        room.id,
        start_index=0,
        count=3,
    )
    await session.commit()

    assert preload_song_ids == [song.platform_song_id]

    refreshed = await crud.get_room_song_by_song_id(session, room.id, song.id)
    assert refreshed is not None
    assert refreshed.temp_url == f"token-{song.id}"
    assert refreshed.expire_at is not None


@pytest.mark.asyncio
async def test_get_room_song_and_song_by_temp_token(session: AsyncSession) -> None:
    room = models.Room(id="ROOM02", title="room")
    song = models.Song(
        platform="qq",
        platform_song_id="qq-mid-token-lookup",
        title="token-lookup-song",
    )
    session.add_all([room, song])
    await session.flush()

    room_song = models.RoomSong(
        room_id=room.id,
        song_id=song.id,
        song_order=1,
        temp_url="db-token-lookup",
    )
    session.add(room_song)
    await session.commit()

    pair = await crud.get_room_song_and_song_by_temp_token(session, "db-token-lookup")
    assert pair is not None
    fetched_room_song, fetched_song = pair
    assert fetched_room_song.room_id == room.id
    assert fetched_room_song.song_id == song.id
    assert fetched_song.id == song.id

    missing = await crud.get_room_song_and_song_by_temp_token(session, "not-exists")
    assert missing is None


@pytest.mark.asyncio
async def test_get_or_create_audio_token_regenerates_when_redis_mapping_missing(
        session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    room = models.Room(id="ROOM03", title="room")
    song = models.Song(
        platform="qq",
        platform_song_id="qq-mid-regenerate",
        title="regenerate-song",
    )
    session.add_all([room, song])
    await session.flush()

    room_song = models.RoomSong(
        room_id=room.id,
        song_id=song.id,
        song_order=1,
        temp_url="stale-token",
        expire_at=datetime.datetime.now(datetime.UTC).replace(tzinfo=None) +
        datetime.timedelta(minutes=30),
    )
    session.add(room_song)
    await session.commit()

    async def _fake_get_song_id_from_token(_token: str) -> int | None:
        return None

    async def _fake_generate_audio_token(_song_id: int) -> str:
        return "new-token"

    monkeypatch.setattr(crud, "get_song_id_from_token", _fake_get_song_id_from_token)
    monkeypatch.setattr(crud, "generate_audio_token", _fake_generate_audio_token)

    token = await crud.get_or_create_audio_token(session, room.id, song.id)
    await session.commit()

    refreshed = await crud.get_room_song_by_song_id(session, room.id, song.id)
    assert token == "new-token"
    assert refreshed is not None
    assert refreshed.temp_url == "new-token"


@pytest.mark.asyncio
async def test_refresh_default_playback_initial_song_follows_latest_song_order(
        session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    room = models.Room(id="ROOM04", title="room")
    song1 = models.Song(platform="qq", platform_song_id="mid-1", title="song-1")
    song2 = models.Song(platform="qq", platform_song_id="mid-2", title="song-2")
    song3 = models.Song(platform="qq", platform_song_id="mid-3", title="song-3")
    session.add_all([room, song1, song2, song3])
    await session.flush()

    rs1 = models.RoomSong(room_id=room.id, song_id=song1.id, song_order=1)
    rs2 = models.RoomSong(room_id=room.id, song_id=song2.id, song_order=2)
    rs3 = models.RoomSong(room_id=room.id, song_id=song3.id, song_order=3)
    session.add_all([rs1, rs2, rs3])
    await session.commit()

    captured_states = []

    async def _fake_set_room_playback_state(room_id: str, playback_state) -> None:
        captured_states.append((room_id, playback_state))

    async def _fake_delete_room_playback_state(_room_id: str) -> None:
        raise AssertionError("song queue 非空时不应删除 playback state")

    async def _fake_get_or_create_audio_token(_session: AsyncSession, _room_id: str,
                                              song_id: int) -> str:
        return f"token-{song_id}"

    monkeypatch.setattr(
        room_songs_router.room_cache,
        "set_room_playback_state",
        _fake_set_room_playback_state,
    )
    monkeypatch.setattr(
        room_songs_router.room_cache,
        "delete_room_playback_state",
        _fake_delete_room_playback_state,
    )
    monkeypatch.setattr(
        room_songs_router.crud,
        "get_or_create_audio_token",
        _fake_get_or_create_audio_token,
    )
    monkeypatch.setattr(room_songs_router, "get_audio_stream_url",
                        lambda token: f"/audio/{token}")

    # 第一次刷新：首曲应为 song1
    await room_songs_router._refresh_default_playback_initial_song(session, room.id)
    room_after_first_refresh = await session.get(models.Room, room.id)
    assert room_after_first_refresh is not None
    assert room_after_first_refresh.current_song_index == 0
    assert captured_states
    assert captured_states[-1][0] == room.id
    assert captured_states[-1][1].audio_url == f"/audio/token-{song1.id}"

    # 模拟再次 shuffle 后首曲变为 song2
    rs1.song_order = 2
    rs2.song_order = 1
    rs3.song_order = 3
    await session.flush()

    await room_songs_router._refresh_default_playback_initial_song(session, room.id)
    room_after_second_refresh = await session.get(models.Room, room.id)
    assert room_after_second_refresh is not None
    assert room_after_second_refresh.current_song_index == 0
    assert captured_states[-1][1].audio_url == f"/audio/token-{song2.id}"
