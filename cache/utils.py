"""Redis utility functions and key generation."""

from __future__ import annotations

import re

from .schemas import PlaybackState

RoomPlaybackState = PlaybackState  # 兼容现有代码

ROOM_TTL_SECONDS = 6 * 60 * 60

ROOM_ID_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _validate_room_id_for_key(room_id: str) -> str:
    """验证 room_id，避免 Redis 键空间注入或冲突。"""
    normalized = room_id.strip()
    if not normalized:
        raise ValueError("room_id must not be empty")
    if not ROOM_ID_KEY_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Invalid room_id for Redis key. Only [A-Za-z0-9_-] are allowed.")
    return normalized


class RedisKeys:
    """Redis key name generation utility class."""

    @staticmethod
    def room_song_queue(room_id: str) -> str:
        """Generate Redis key for room song queue."""
        safe_room_id = _validate_room_id_for_key(room_id)
        return f"room:{safe_room_id}:song_queue"

    @staticmethod
    def playback_state(room_id: str) -> str:
        """Generate Redis key for room playback state."""
        safe_room_id = _validate_room_id_for_key(room_id)
        return f"room:{safe_room_id}:playback"

    @staticmethod
    def answer_queue(room_id: str) -> str:
        """Generate Redis key for room answer queue."""
        safe_room_id = _validate_room_id_for_key(room_id)
        return f"room:{safe_room_id}:answer_queue"

    @staticmethod
    def answer_queue_player_index(room_id: str) -> str:
        """Generate Redis key for answer queue player index."""
        safe_room_id = _validate_room_id_for_key(room_id)
        return f"room:{safe_room_id}:answer_queue:player_index"

    @staticmethod
    def answer_queue_tail_player(room_id: str) -> str:
        """Generate Redis key for answer queue tail player id boundary."""
        safe_room_id = _validate_room_id_for_key(room_id)
        return f"room:{safe_room_id}:answer_queue:tail_player"

    @staticmethod
    def answerer(room_id: str) -> str:
        """Generate Redis key for room answerer."""
        safe_room_id = _validate_room_id_for_key(room_id)
        return f"room:{safe_room_id}:answer_queue:answerer"
