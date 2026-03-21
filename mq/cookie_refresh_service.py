"""Cookie refresh service for automatic credential renewal."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CookieRefreshLog
from db.session import session_scope
from utils.cookie_pool import CookiePoolManager

logger = logging.getLogger(__name__)

REFRESH_LOG_TTL = 86400 * 7  # 7 days


class CookieRefreshService:
    """Service for managing cookie refresh operations."""

    def __init__(self, pool: CookiePoolManager):
        """Initialize refresh service."""
        self.pool = pool

    async def check_and_refresh_expired(self) -> dict[str, Any]:
        """Check all cookies and refresh expired ones."""
        results = {
            "checked": 0,
            "refreshed": 0,
            "failed": 0,
            "details": [],
        }

        for entry in self.pool.list_all():
            results["checked"] += 1

            is_expired = await entry.is_expired()
            can_refresh = await entry.can_refresh()

            logger.info(
                f"Cookie {entry.cookie_id}: expired={is_expired}, can_refresh={can_refresh}"
            )

            if is_expired and can_refresh:
                old_expired = entry.credential.expired_at
                success = await entry.refresh()
                new_expired = entry.credential.expired_at

                if success:
                    results["refreshed"] += 1
                    detail = {
                        "cookie_id": entry.cookie_id,
                        "status": "success",
                        "old_expired_at": old_expired,
                        "new_expired_at": new_expired,
                    }
                else:
                    results["failed"] += 1
                    detail = {
                        "cookie_id": entry.cookie_id,
                        "status": "failed",
                        "error": entry.error_message,
                    }

                results["details"].append(detail)
                await self._log_refresh(
                    entry.cookie_id,
                    "success" if success else "failed",
                    old_expired,
                    new_expired if success else None,
                    entry.error_message if not success else None,
                )

        logger.info(f"Refresh check completed: {results}")
        return results

    async def _log_refresh(
        self,
        cookie_id: str,
        status: str,
        old_expired_at: int | None,
        new_expired_at: int | None,
        error_message: str | None,
    ) -> None:
        """Log refresh result to database."""
        try:
            async with session_scope() as session:
                log = CookieRefreshLog(
                    cookie_id=cookie_id,
                    status=status,
                    old_expired_at=old_expired_at,
                    new_expired_at=new_expired_at,
                    error_message=error_message,
                )
                session.add(log)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to log refresh: {e}")

    async def get_recent_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent refresh logs."""
        try:
            async with session_scope() as session:
                from sqlalchemy import select, desc

                stmt = select(CookieRefreshLog).order_by(
                    desc(CookieRefreshLog.timestamp)).limit(limit)
                result = await session.execute(stmt)
                logs = []
                for log in result.scalars():
                    logs.append({
                        "timestamp": log.timestamp.isoformat(),
                        "cookie_id": log.cookie_id,
                        "status": log.status,
                        "old_expired_at": log.old_expired_at,
                        "new_expired_at": log.new_expired_at,
                        "error_message": log.error_message,
                    })
                return logs
        except Exception as e:
            logger.error(f"Failed to get refresh logs: {e}")
            return []
