from __future__ import annotations

from datetime import datetime, timezone
from .schemas import AnswerQueueItem, PlaybackState

RoomPlaybackState = PlaybackState  # 兼容现有代码

ROOM_TTL_SECONDS = 6 * 60 * 60


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RedisKeys:
    """Redis 键名生成类"""

    @staticmethod
    def room(room_id: str) -> str:
        return f"room:{room_id}"

    @staticmethod
    def room_players(room_id: str) -> str:
        return f"room:{room_id}:players"

    @staticmethod
    def room_player_status(room_id: str, player_id: str) -> str:
        return f"room:{room_id}:player:{player_id}"

    @staticmethod
    def room_song_queue(room_id: str) -> str:
        return f"room:{room_id}:song_queue"

    @staticmethod
    def room_online(room_id: str) -> str:
        return f"room:{room_id}:online"

    @staticmethod
    def playback_state(room_id: str) -> str:
        return f"room:{room_id}:playback"

    @staticmethod
    def answer_queue(room_id: str) -> str:
        return f"room:{room_id}:answer_queue"
