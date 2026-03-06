from __future__ import annotations
"""
Song request/response schemas.

Handles:
- Song metadata responses
- Song cache/download status updates
- Song batch operations
"""

from datetime import datetime
from typing import Any, Optional, List
from pydantic import BaseModel, ConfigDict, Field


class SongBase(BaseModel):
    """Base schema for song data."""

    platform: Optional[str] = Field(
        default=None, description="Platform identifier (e.g., 'qqmusic', 'netease')")
    platform_song_id: Optional[str] = Field(default=None,
                                            description="Platform-specific song ID")
    title: Optional[str] = Field(default=None, description="Song title")
    subtitle: Optional[str] = Field(default=None,
                                    description="Song subtitle/description")
    artist: Optional[str] = Field(default=None, description="Artist/performer name")
    album_name: Optional[str] = Field(default=None, description="Album name")
    album_id: Optional[int] = Field(
        default=None, description="Foreign key referencing the album table")
    cover_url: Optional[str] = Field(default=None, description="Cover image URL")
    audio_url: Optional[str] = Field(default=None, description="Audio stream URL")
    cached_path: Optional[str] = Field(
        default=None,
        description="Local filesystem path where audio is cached (null if not cached)")

    model_config = ConfigDict(from_attributes=True)


class SongCreate(SongBase):
    """Schema for creating a new song."""
    pass


class SongResponse(SongBase):
    """Response schema for song details."""

    id: int = Field(description="Database ID")
    cached: bool = Field(description="Whether this song has been cached locally",
                         default=False,
                         alias="is_cached")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "platform": "qqmusic",
                "platform_song_id": "004R6Kl32YDHxe",
                "title": "Song Title",
                "subtitle": "Subtitle",
                "artist": "Artist Name",
                "album_name": "Album",
                "cover_url": "https://example.com/cover.jpg",
                "audio_url": "https://stream.example.com/song.mp3",
                "cached_path": "/assets/audio/004R6Kl32YDHxe.mp3",
                "cached": True,
                "metadata_json": None
            }
        })

    @property
    def is_cached(self) -> bool:
        """Compute whether song is cached based on cached_path."""
        return self.cached_path is not None and len(self.cached_path) > 0


class SongListResponse(BaseModel):
    """Response schema for paginated song list."""

    total: int = Field(description="Total number of songs available")
    list: List[SongResponse] = Field(description="Current page songs")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total":
                    120,
                "list": [{
                    "id": 1,
                    "platform": "qqmusic",
                    "platform_song_id": "004R6Kl32YDHxe",
                    "title": "Song Title",
                    "artist": "Artist Name",
                    "cached": True
                }]
            }
        })


class SongCacheUpdateRequest(BaseModel):
    """Request schema for updating a song's cached audio path."""

    song_id: int = Field(description="Database ID of the song to update")
    cached_path: str = Field(
        description="Filesystem path where audio file is now stored")
    format: Optional[str] = Field(default="mp3", description="Audio format/codec")
    file_size_bytes: Optional[int] = Field(default=None,
                                           ge=0,
                                           description="Optional: file size in bytes")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "song_id": 1,
                "cached_path": "/assets/audio/004R6Kl32YDHxe.mp3",
                "format": "mp3",
                "file_size_bytes": 5242880
            }
        })


class TaskResponse(BaseModel):
    """Response schema for task status."""

    task_id: str = Field(description="Unique task identifier")
    task_name: str = Field(description="Name of the task")
    status: str = Field(description="Current task status")
    result: Optional[dict[str, Any]] = Field(default=None,
                                             description="Task result JSON, if any")
    created_at: Optional[datetime] = Field(default=None,
                                           description="Task creation timestamp")
    updated_at: Optional[datetime] = Field(default=None,
                                           description="Task last update timestamp")
    huey_task_id: Optional[str] = Field(
        default=None, description="Optional Huey task ID for queue tracking")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "task_id": "550e8400-e29b-41d4-a716-446655440000",
                "task_name": "fetch_songlist",
                "status": "pending",
                "result": None,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "huey_task_id": "550e8400-e29b-41d4-a716-446655440000"
            }
        })


class WebSocketErrorResponse(BaseModel):
    """Error response schema for WebSocket events."""

    type: str = Field(default="error", description="Message type")
    event: Optional[str] = Field(default=None,
                                 description="Event type that caused the error")
    reason: str = Field(description="Error reason/message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "error",
                "event": "play",
                "reason": "Only owner can control playback"
            }
        })


class HttpErrorResponse(BaseModel):
    """Error response schema for HTTP API errors."""

    error: str = Field(description="Error type/message")
    detail: Optional[str] = Field(default=None, description="Additional error details")
    path: Optional[str] = Field(default=None,
                                description="Request path that caused the error")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "Not found",
                "detail": "The requested resource was not found",
                "path": "/api/nonexistent"
            }
        })


class SonglistFetchResult(BaseModel):
    """Result schema for songlist fetch operation."""

    songlist: dict[str, Any] = Field(description="Fetched songlist metadata")
    songs: list[dict[str, Any]] = Field(description="List of songs in the songlist")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "songlist": {
                    "platform": "qqmusic",
                    "platform_songlist_id": "123456789",
                    "title": "My Favorite Songs",
                    "creator_name": "John Doe",
                    "cover_url": "https://example.com/cover.jpg"
                },
                "songs": [{
                    "platform": "qqmusic",
                    "platform_song_id": "001",
                    "title": "Song 1",
                    "artist": "Artist 1"
                }, {
                    "platform": "qqmusic",
                    "platform_song_id": "002",
                    "title": "Song 2",
                    "artist": "Artist 2"
                }]
            }
        })


class SongItem(SongBase):
    id: int
    order: Optional[int] = Field(default=None, description="Order in the song queue")

    model_config = ConfigDict(from_attributes=True)
