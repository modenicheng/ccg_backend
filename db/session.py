from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base


def _normalize_database_url(raw_url: str | None) -> str:
    """Normalize database URL and provide a SQLite default for local dev."""
    if raw_url:
        url = raw_url.strip()
    else:
        default_db_path = Path("data") / "game.db"
        default_db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{default_db_path.as_posix()}"

    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL"))
DATABASE_ECHO = os.getenv("DATABASE_ECHO", "false").lower() in {"1", "true", "yes", "on"}

engine = create_async_engine(
    DATABASE_URL,
    echo=DATABASE_ECHO,
    future=True,
)

if DATABASE_URL.startswith("sqlite+"):
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragma_on_connect(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
        cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Create all tables declared in ORM models."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_db() -> None:
    """Drop all ORM tables (for tests/dev reset)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: provide an async database session."""
    async with AsyncSessionLocal() as session:
        yield session


async def ping_db() -> bool:
    """Simple connectivity probe."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
