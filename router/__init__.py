from __future__ import annotations
from .room import room_router
from .tags import tag_router
from .song import song_router
from .songlist import songlist_router
from .room_songs import room_songs_router
from .audio_stream import audio_stream_router

__all__ = [
    "room_router", "tag_router", "song_router", "songlist_router",
    "room_songs_router", "audio_stream_router"
]
