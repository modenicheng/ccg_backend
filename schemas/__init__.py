from __future__ import annotations
"""
Data schemas for request/response validation and serialization.

This module provides Pydantic models for:
- Request validation (HTTP endpoints, task inputs)
- Response serialization (API responses)
- Type hints across the application
"""

from .songlist import (
    SonglistCreateRequest,
    SonglistResponse,
    SonglistFetchRequest,
)
from .song import (
    SongResponse,
    SongCacheUpdateRequest,
)
from .task import (
    TaskDownloadRequest,
    TaskDownloadResponse,
)
from .room import (
    CreateRoomRequest,
    CreateRoomResponse,
    RoomInfoResponse,
    PatchRoomRequest,
)

__all__ = [
    # Songlist schemas
    "SonglistCreateRequest",
    "SonglistResponse",
    "SonglistFetchRequest",
    # Song schemas
    "SongResponse",
    "SongCacheUpdateRequest",
    # Task schemas
    "TaskDownloadRequest",
    "TaskDownloadResponse",
    # Room schemas
    "CreateRoomRequest",
    "CreateRoomResponse",
    "RoomInfoResponse",
    "PatchRoomRequest",
]
