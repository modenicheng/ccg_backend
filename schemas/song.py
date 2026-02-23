"""
Song request/response schemas.

Handles:
- Song metadata responses
- Song cache/download status updates
- Song batch operations
"""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class SongResponse(BaseModel):
    """Response schema for song details."""
    
    id: int = Field(description="Database ID")
    platform: Optional[str] = Field(description="Platform identifier")
    platform_song_id: Optional[str] = Field(description="Platform-specific song ID")
    title: Optional[str] = Field(description="Song title")
    subtitle: Optional[str] = Field(description="Song subtitle/description")
    artist: Optional[str] = Field(description="Artist/performer name")
    album_name: Optional[str] = Field(description="Album name")
    cover_url: Optional[str] = Field(description="Cover image URL")
    audio_url: Optional[str] = Field(description="Audio stream URL")
    cached_path: Optional[str] = Field(
        default=None,
        description="Local filesystem path where audio is cached (null if not cached)"
    )
    metadata_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="Additional metadata"
    )
    is_cached: bool = Field(
        description="Whether this song has been cached locally",
        default=False
    )

    class Config:
        from_attributes = True
        json_schema_extra = {
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
                "is_cached": True,
                "metadata_json": None
            }
        }

    @property
    def is_cached(self) -> bool:
        """Compute whether song is cached based on cached_path."""
        return self.cached_path is not None and len(self.cached_path) > 0


class SongCacheUpdateRequest(BaseModel):
    """Request schema for updating a song's cached audio path."""
    
    song_id: int = Field(
        description="Database ID of the song to update"
    )
    cached_path: str = Field(
        description="Filesystem path where audio file is now stored"
    )
    format: Optional[str] = Field(
        default="mp3",
        description="Audio format/codec"
    )
    file_size_bytes: Optional[int] = Field(
        default=None,
        ge=0,
        description="Optional: file size in bytes"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "song_id": 1,
                "cached_path": "/assets/audio/004R6Kl32YDHxe.mp3",
                "format": "mp3",
                "file_size_bytes": 5242880
            }
        }
