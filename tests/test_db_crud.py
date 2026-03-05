from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db import crud, models


@pytest_asyncio.fixture
async def session(tmp_path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "test_crud.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}",
                                 future=True)

    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_factory() as db_session:
        yield db_session

    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_or_update_songlist_updates_existing_row(
        session: AsyncSession) -> None:
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
        session: AsyncSession) -> None:
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

    links = (await
             session.execute(select(models.SonglistSong))).scalars().all()

    assert updated.id == song.id
    assert updated.title == "updated"
    assert len(links) == 1
    assert links[0].songlist_id == songlist.id
    assert links[0].song_id == song.id


@pytest.mark.asyncio
async def test_create_or_update_song_does_not_duplicate_songlist_link(
        session: AsyncSession) -> None:
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

    links = (await
             session.execute(select(models.SonglistSong))).scalars().all()

    assert song.id is not None
    assert len(links) == 1
    assert links[0].songlist_id == songlist.id
    assert links[0].song_id == song.id


@pytest.mark.asyncio
async def test_create_or_update_songs_bulk_upserts(
        session: AsyncSession) -> None:
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
                "title": "song-1-updated"
            },
            {
                "platform": "qq",
                "platform_song_id": "bulk-song-3",
                "title": "song-3"
            },
        ],
    )
    await session.commit()

    songs = (await session.execute(
        select(models.Song).order_by(models.Song.platform_song_id)
    )).scalars().all()
    links = (await
             session.execute(select(models.SonglistSong))).scalars().all()

    assert len(updated) == 2
    assert len(songs) == 3
    assert songs[0].platform_song_id == "bulk-song-1"
    assert songs[0].title == "song-1-updated"
    assert len(links) == 3


@pytest.mark.asyncio
async def test_create_or_update_songs_accepts_qq_song_shape(
        session: AsyncSession) -> None:
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
        session: AsyncSession) -> None:
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
