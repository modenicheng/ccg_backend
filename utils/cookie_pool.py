"""Cookie pool management for QQMusic credential rotation."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import qqmusic_api as qapi

from utils.cookie import parse_cookie_string

logger = logging.getLogger(__name__)


@dataclass
class CookieEntry:
    """Represents a single cookie entry in the pool."""

    cookie_id: str  # SHA256 hash of cookie string
    cookie_str: str  # Original cookie string
    credential: qapi.Credential  # Parsed credential
    created_at: datetime = field(default_factory=datetime.now)
    last_used_at: datetime | None = None
    failed_count: int = 0
    last_failed_at: datetime | None = None
    is_healthy: bool = True
    error_message: str | None = None

    @staticmethod
    def generate_id(cookie_str: str) -> str:
        """Generate SHA256 hash ID for cookie string."""
        return hashlib.sha256(cookie_str.encode()).hexdigest()[:16]

    def mark_used(self) -> None:
        """Mark cookie as used."""
        self.last_used_at = datetime.now()

    def mark_failed(self, error_msg: str | None = None) -> None:
        """Mark cookie as failed."""
        self.failed_count += 1
        self.last_failed_at = datetime.now()
        self.error_message = error_msg
        if self.failed_count >= 3:  # 失败3次后标记为不健康
            self.is_healthy = False

    def reset_health(self) -> None:
        """Reset health status."""
        self.failed_count = 0
        self.is_healthy = True
        self.error_message = None

    async def is_expired(self) -> bool:
        """Check if credential is expired."""
        try:
            return await self.credential.is_expired()
        except Exception as e:
            logger.warning(
                f"Error checking credential expiry for {self.cookie_id}: {e}")
            return False

    async def can_refresh(self) -> bool:
        """Check if credential can be refreshed."""
        try:
            return await self.credential.can_refresh()
        except Exception as e:
            logger.warning(
                f"Error checking refresh capability for {self.cookie_id}: {e}")
            return False

    async def refresh(self) -> bool:
        """Attempt to refresh credential."""
        try:
            result = await self.credential.refresh()
            if result:
                self.reset_health()
            return result
        except Exception as e:
            logger.error(f"Error refreshing credential for {self.cookie_id}: {e}")
            self.mark_failed(str(e))
            return False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "cookie_id":
                self.cookie_id,
            "created_at":
                self.created_at.isoformat(),
            "last_used_at":
                self.last_used_at.isoformat() if self.last_used_at else None,
            "failed_count":
                self.failed_count,
            "last_failed_at":
                self.last_failed_at.isoformat() if self.last_failed_at else None,
            "is_healthy":
                self.is_healthy,
            "error_message":
                self.error_message,
        }


class CookiePoolManager:
    """Manages a pool of QQMusic cookies with rotation support."""

    def __init__(self):
        """Initialize cookie pool."""
        self.pool: dict[str, CookieEntry] = {}
        self.primary_cookie_id: str | None = None

    async def load_from_sources(
        self,
        single_cookie: str | None = None,
        cookie_list: list[str] | None = None,
        db_cookies: list[dict[str, Any]] | None = None,
    ) -> None:
        """Load cookies from multiple sources with priority: db > list > single."""
        logger.debug(
            "load_from_sources called: db_cookies=%d, cookie_list=%d, single_cookie=%s",
            len(db_cookies or []),
            len(cookie_list or []),
            "present" if single_cookie else "absent",
        )

        # 优先级：db > list > single
        cookie_strings: list[str] = []
        source_map: dict[str, str] = {}  # cookie_hash -> source for logging

        if db_cookies:
            for c in db_cookies:
                cookie_str = c.get("cookie")
                if cookie_str:
                    cookie_strings.append(cookie_str)
                    source_map[CookieEntry.generate_id(cookie_str)] = (
                        f"db (source={c.get('source')})")

        if cookie_list:
            for c in cookie_list:
                if c:
                    cookie_strings.append(c)
                    source_map[CookieEntry.generate_id(c)] = "config"

        if single_cookie:
            cookie_strings.append(single_cookie)
            source_map[CookieEntry.generate_id(single_cookie)] = "env"

        if not cookie_strings:
            logger.warning("No cookies loaded from any source")
            return

        # 去重并加载
        seen: set[str] = set()
        loaded_count = 0
        for cookie_str in cookie_strings:
            if cookie_str in seen or not cookie_str.strip():
                continue
            seen.add(cookie_str)

            try:
                cookies_dict = parse_cookie_string(cookie_str)
                if cookies_dict:
                    credential = qapi.Credential.from_cookies_dict(cookies_dict)
                    cookie_id = CookieEntry.generate_id(cookie_str)
                    entry = CookieEntry(
                        cookie_id=cookie_id,
                        cookie_str=cookie_str,
                        credential=credential,
                    )
                    self.pool[cookie_id] = entry
                    loaded_count += 1

                    if self.primary_cookie_id is None:
                        self.primary_cookie_id = cookie_id
                        logger.info(
                            "Set primary cookie: %s (from %s)",
                            cookie_id,
                            source_map.get(cookie_id, "unknown"),
                        )
                    else:
                        logger.info(
                            "Loaded cookie: %s (from %s)",
                            cookie_id,
                            source_map.get(cookie_id, "unknown"),
                        )
            except Exception as e:
                logger.error(f"Failed to parse cookie: {e}", exc_info=True)

        logger.info(
            "Successfully loaded %d/%d unique cookies into pool",
            loaded_count,
            len(seen),
        )

    def get_primary(self) -> CookieEntry | None:
        """Get primary (first) cookie."""
        if self.primary_cookie_id and self.primary_cookie_id in self.pool:
            return self.pool[self.primary_cookie_id]
        return None

    def get_by_id(self, cookie_id: str) -> CookieEntry | None:
        """Get cookie by ID."""
        return self.pool.get(cookie_id)

    def list_all(self) -> list[CookieEntry]:
        """List all cookies."""
        return list(self.pool.values())

    def list_healthy(self) -> list[CookieEntry]:
        """List all healthy cookies."""
        return [e for e in self.pool.values() if e.is_healthy]

    async def list_not_expired(self) -> list[CookieEntry]:
        """List cookies that are not expired."""
        result = []
        for entry in self.pool.values():
            if not await entry.is_expired():
                result.append(entry)
        return result

    async def persist_new_cookies_to_db(self, session: "AsyncSession") -> int:
        """
        Persist any cookies that are not yet in the database.

        This is called after loading cookies to ensure they are persisted,
        which is particularly important for the single-cookie case.

        Args:
            session: Database session

        Returns:
            Number of new cookies persisted
        """
        from db.crud.cookie_crud import add_or_update_cookie

        logger.debug("Persisting cookies to database...")
        persisted_count = 0

        for entry in self.pool.values():
            try:
                # Always use add_or_update to ensure idempotency
                _, cookie_hash = await add_or_update_cookie(
                    session,
                    entry.cookie_str,
                    source="system",  # Loaded from environment/config
                )
                logger.debug("Persisted cookie to database: %s", cookie_hash)
                persisted_count += 1
            except Exception as e:
                logger.error(
                    "Failed to persist cookie %s to database: %s",
                    entry.cookie_id,
                    e,
                    exc_info=True,
                )

        if persisted_count > 0:
            logger.info("Persisted %d new/updated cookies to database", persisted_count)

        return persisted_count

    def to_dict(self) -> dict[str, Any]:
        """Convert pool to dictionary."""
        return {cookie_id: entry.to_dict() for cookie_id, entry in self.pool.items()}
