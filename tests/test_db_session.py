from __future__ import annotations

from typing import AsyncGenerator, AsyncIterator, cast

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import db.session as session_module
from db import crud, models


@pytest_asyncio.fixture
async def isolated_session_factory(
        tmp_path,
        monkeypatch) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "test_session.db"
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

    monkeypatch.setattr(session_module, "AsyncSessionLocal", session_factory)
    yield session_factory

    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_db_session_commits_on_success(
        isolated_session_factory) -> None:
    gen = cast(AsyncGenerator[AsyncSession, None], session_module.get_db())
    session = await anext(gen)

    session.add(
        models.Song(platform="qq", platform_song_id="commit-1", title="ok"))

    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)

    async with isolated_session_factory() as check_session:
        songs = (await
                 check_session.execute(select(models.Song))).scalars().all()

    assert len(songs) == 1
    assert songs[0].platform_song_id == "commit-1"


@pytest.mark.asyncio
async def test_get_db_session_rolls_back_on_error(
        isolated_session_factory) -> None:
    gen = cast(AsyncGenerator[AsyncSession, None], session_module.get_db())
    session = await anext(gen)

    session.add(
        models.Song(platform="qq", platform_song_id="rollback-1",
                    title="nope"))

    with pytest.raises(RuntimeError, match="boom"):
        await gen.athrow(RuntimeError("boom"))

    async with isolated_session_factory() as check_session:
        songs = (await
                 check_session.execute(select(models.Song))).scalars().all()

    assert songs == []


@pytest.mark.asyncio
async def test_session_scope_commits_and_rolls_back(
        isolated_session_factory) -> None:
    async with session_module.session_scope() as session:
        session.add(
            models.Song(platform="qq",
                        platform_song_id="scope-commit",
                        title="ok"))

    with pytest.raises(ValueError, match="rollback"):
        async with session_module.session_scope() as session:
            session.add(
                models.Song(platform="qq",
                            platform_song_id="scope-rollback",
                            title="bad"))
            raise ValueError("rollback")

    async with isolated_session_factory() as check_session:
        songs = (await
                 check_session.execute(select(models.Song))).scalars().all()

    assert len(songs) == 1
    assert songs[0].platform_song_id == "scope-commit"


@pytest.mark.asyncio
async def test_session_scope_with_crud_commits_atomically(
        isolated_session_factory) -> None:
    async with session_module.session_scope() as session:
        songlist = await crud.create_or_update_songlist(
            session,
            platform="qq",
            platform_songlist_id=1,
            title="tx-list",
        )
        await crud.create_or_update_song(
            session,
            songlist_id=songlist.id,
            platform="qq",
            platform_song_id="tx-song-1",
            title="tx-song",
        )

    async with isolated_session_factory() as check_session:
        songlists = (await check_session.execute(select(models.Songlist)
                                                 )).scalars().all()
        songs = (await
                 check_session.execute(select(models.Song))).scalars().all()
        links = (await check_session.execute(select(models.SonglistSong)
                                             )).scalars().all()

    assert len(songlists) == 1
    assert len(songs) == 1
    assert len(links) == 1
    assert links[0].songlist_id == songlists[0].id
    assert links[0].song_id == songs[0].id


@pytest.mark.asyncio
async def test_session_scope_with_crud_rolls_back_atomically(
        isolated_session_factory) -> None:
    with pytest.raises(RuntimeError, match="force rollback"):
        async with session_module.session_scope() as session:
            songlist = await crud.create_or_update_songlist(
                session,
                platform="qq",
                platform_songlist_id=2,
                title="tx-list",
            )
            await crud.create_or_update_song(
                session,
                songlist_id=songlist.id,
                platform="qq",
                platform_song_id="tx-song-2",
                title="tx-song",
            )
            raise RuntimeError("force rollback")

    async with isolated_session_factory() as check_session:
        songlists = (await check_session.execute(select(models.Songlist)
                                                 )).scalars().all()
        songs = (await
                 check_session.execute(select(models.Song))).scalars().all()
        links = (await check_session.execute(select(models.SonglistSong)
                                             )).scalars().all()

    assert songlists == []
    assert songs == []
    assert links == []
