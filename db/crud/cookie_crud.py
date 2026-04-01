"""CRUD operations for cookie configuration and logging."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CookieConfig, CookieRotationLog, CookieRefreshLog

logger = logging.getLogger(__name__)


async def add_or_update_cookie(
    session: AsyncSession,
    cookie_content: str,
    source: str = "manual",
) -> tuple[int, str]:
    """
    Add or update a cookie in the database.

    Args:
        session: Database session
        cookie_content: Raw cookie string
        source: Source of cookie ("manual", "env", "yaml", "sso")

    Returns:
        Tuple of (cookie_id from db, cookie_hash)
    """
    cookie_hash = hashlib.sha256(cookie_content.encode()).hexdigest()[:16]

    # 尝试找到已存在的cookie
    stmt = select(CookieConfig).where(CookieConfig.cookie_hash == cookie_hash)
    existing = await session.scalar(stmt)

    if existing:
        existing.source = source
        existing.is_active = True
        logger.debug("Updated existing cookie: %s", cookie_hash)
    else:
        new_cookie = CookieConfig(
            cookie_hash=cookie_hash,
            cookie_content=cookie_content,
            source=source,
            is_active=True,
        )
        session.add(new_cookie)
        logger.debug("Added new cookie to database: %s", cookie_hash)
        existing = new_cookie

    await session.flush()
    return existing.id, cookie_hash


async def get_all_active_cookies(session: AsyncSession) -> list[dict[str, Any]]:
    """Get all active cookies from database."""
    stmt = select(CookieConfig).where(CookieConfig.is_active is True)
    results = await session.scalars(stmt)

    cookies = []
    for cookie in results:
        cookies.append({
            "cookie": cookie.cookie_content,
            "cookie_hash": cookie.cookie_hash,
            "source": cookie.source,
            "id": cookie.id,
        })

    logger.info("Retrieved %d active cookies from database", len(cookies))
    return cookies


async def get_cookie_by_hash(
    session: AsyncSession,
    cookie_hash: str,
) -> CookieConfig | None:
    """Get cookie by hash."""
    stmt = select(CookieConfig).where(CookieConfig.cookie_hash == cookie_hash)
    return await session.scalar(stmt)


async def mark_cookie_inactive(
    session: AsyncSession,
    cookie_hash: str,
) -> None:
    """Mark a cookie as inactive."""
    cookie = await get_cookie_by_hash(session, cookie_hash)
    if cookie:
        cookie.is_active = False
        logger.info("Marked cookie as inactive: %s", cookie_hash)
        await session.flush()


async def log_rotation_event(
    session: AsyncSession,
    from_cookie_id: str | None,
    to_cookie_id: str,
    reason: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Log a cookie rotation event."""
    log_entry = CookieRotationLog(
        from_cookie_id=from_cookie_id,
        to_cookie_id=to_cookie_id,
        reason=reason,
        context_json=context,
    )
    session.add(log_entry)
    logger.info(
        "Logged rotation: %s -> %s (reason: %s)",
        from_cookie_id,
        to_cookie_id,
        reason,
    )
    await session.flush()


async def log_refresh_event(
    session: AsyncSession,
    cookie_id: str,
    status: str,
    old_expired_at: int | None = None,
    new_expired_at: int | None = None,
    error_message: str | None = None,
) -> None:
    """Log a cookie refresh event."""
    log_entry = CookieRefreshLog(
        cookie_id=cookie_id,
        status=status,
        old_expired_at=old_expired_at,
        new_expired_at=new_expired_at,
        error_message=error_message,
    )
    session.add(log_entry)
    logger.info(
        "Logged refresh: %s status=%s",
        cookie_id,
        status,
    )
    await session.flush()
