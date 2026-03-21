"""Cookie rotation manager for QQMusic API calls."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from utils.cookie_pool import CookieEntry, CookiePoolManager

logger = logging.getLogger(__name__)


class RotationStrategy(str, Enum):
    """Cookie rotation strategies."""

    ROUND_ROBIN = "round_robin"
    PRIMARY_FIRST = "primary_first"


class FailurePolicy(str, Enum):
    """Failure handling policies."""

    RETRY_SAME = "retry_same"  # 重试同一个 Cookie
    IMMEDIATE_ROTATE = "immediate_rotate"  # 立即轮换
    MARK_AND_ROTATE = "mark_and_rotate"  # 标记失效后轮换


@dataclass
class RotationState:
    """Current rotation state."""

    current_index: int = 0
    current_cookie_id: str | None = None
    last_rotated_at: datetime = field(default_factory=datetime.now)
    rotation_count: int = 0
    cookies: list[str] = field(default_factory=list)  # 所有 cookie ID 列表

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for Redis serialization."""
        return {
            "current_index": self.current_index,
            "current_cookie_id": self.current_cookie_id,
            "last_rotated_at": self.last_rotated_at.isoformat(),
            "rotation_count": self.rotation_count,
            "cookies": self.cookies,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RotationState:
        """Create from dictionary."""
        return cls(
            current_index=data.get("current_index", 0),
            current_cookie_id=data.get("current_cookie_id"),
            last_rotated_at=datetime.fromisoformat(
                data.get("last_rotated_at",
                         datetime.now().isoformat())),
            rotation_count=data.get("rotation_count", 0),
            cookies=data.get("cookies", []),
        )


class CookieRotationManager:
    """Manages cookie rotation with configurable strategy and failure handling."""

    def __init__(
        self,
        pool: CookiePoolManager,
        strategy: RotationStrategy = RotationStrategy.ROUND_ROBIN,
        failure_policy: FailurePolicy = FailurePolicy.MARK_AND_ROTATE,
    ):
        """Initialize rotation manager."""
        self.pool = pool
        self.strategy = strategy
        self.failure_policy = failure_policy
        self.state = RotationState()
        self._init_state()

    def _init_state(self) -> None:
        """Initialize state based on pool."""
        self.state.cookies = [e.cookie_id for e in self.pool.list_all()]

        if self.state.cookies:
            self.state.current_index = 0
            self.state.current_cookie_id = self.state.cookies[0]

    def get_current(self) -> CookieEntry | None:
        """Get current cookie."""
        if self.state.current_cookie_id:
            return self.pool.get_by_id(self.state.current_cookie_id)
        return None

    def rotate(self, reason: str = "manual") -> CookieEntry | None:
        """Rotate to next cookie."""
        if not self.state.cookies:
            logger.warning("No cookies available for rotation")
            return None

        # 轮流策略
        if self.strategy == RotationStrategy.ROUND_ROBIN:
            self.state.current_index = (self.state.current_index + 1) % len(
                self.state.cookies)
            self.state.current_cookie_id = self.state.cookies[self.state.current_index]

        self.state.last_rotated_at = datetime.now()
        self.state.rotation_count += 1

        logger.info(
            f"Rotated cookie to {self.state.current_cookie_id} (reason: {reason})")

        current = self.get_current()
        if current:
            current.mark_used()
        return current

    def mark_current_failed(self, error_msg: str | None = None) -> None:
        """Mark current cookie as failed."""
        current = self.get_current()
        if current:
            current.mark_failed(error_msg)
            logger.warning(f"Marked cookie {current.cookie_id} as failed: {error_msg}")

    def should_rotate_on_failure(self) -> bool:
        """Determine if should rotate based on failure policy."""
        if self.failure_policy == FailurePolicy.IMMEDIATE_ROTATE:
            return True

        if self.failure_policy == FailurePolicy.MARK_AND_ROTATE:
            current = self.get_current()
            if current and not current.is_healthy:
                return True

        return False

    def get_state_dict(self) -> dict[str, Any]:
        """Get current state as dictionary."""
        return self.state.to_dict()

    def load_state_dict(self, data: dict[str, Any]) -> None:
        """Load state from dictionary."""
        self.state = RotationState.from_dict(data)
