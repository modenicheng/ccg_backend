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
        # 优先级：db > list > single
        cookie_strings: list[str] = []

        if db_cookies:
            cookie_strings.extend(
                [c.get("cookie") for c in db_cookies if c.get("cookie")])

        if cookie_list:
            cookie_strings.extend([c for c in cookie_list if c])

        if single_cookie:
            cookie_strings.append(single_cookie)

        if not cookie_strings:
            logger.warning("No cookies loaded from any source")
            return

        # 去重并加载
        seen: set[str] = set()
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

                    if self.primary_cookie_id is None:
                        self.primary_cookie_id = cookie_id

                    logger.info(f"Loaded cookie: {cookie_id}")
            except Exception as e:
                logger.error(f"Failed to parse cookie: {e}")

        logger.info(f"Loaded {len(self.pool)} cookies into pool")

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

    def to_dict(self) -> dict[str, Any]:
        """Convert pool to dictionary."""
        return {cookie_id: entry.to_dict() for cookie_id, entry in self.pool.items()}
