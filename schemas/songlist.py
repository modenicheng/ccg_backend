"""
Songlist request/response schemas.

Handles:
- Songlist import/creation requests
- Songlist fetch operations
- Songlist API responses
"""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class SonglistFetchRequest(BaseModel):
    """Request schema for fetching a songlist from a platform."""
    
    platform_songlist_id: str = Field(
        ...,
        description="Platform-specific songlist ID (e.g., '123456789' for QQ Music)"
    )
    platform: str = Field(
        default="qqmusic",
        description="Platform identifier (e.g., 'qqmusic', 'netease')"
    )
    max_retries: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional override for max retry attempts"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "platform_songlist_id": "123456789",
                "platform": "qqmusic",
                "max_retries": 5
            }
        }


class SonglistCreateRequest(BaseModel):
    """Request schema for creating/importing a songlist."""
    
    platform: str = Field(
        description="Platform identifier"
    )
    platform_songlist_id: str = Field(
        description="Platform-specific songlist ID"
    )
    title: Optional[str] = Field(
        default=None,
        description="Songlist title"
    )
    creator_name: Optional[str] = Field(
        default=None,
        description="Creator/author name"
    )
    cover_url: Optional[str] = Field(
        default=None,
        description="Cover image URL"
    )
    metadata_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="Additional metadata as JSON"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "platform": "qqmusic",
                "platform_songlist_id": "123456789",
                "title": "My Favorite Songs",
                "creator_name": "John Doe",
                "cover_url": "https://example.com/cover.jpg",
                "metadata_json": {"description": "A collection of classics"}
            }
        }


class SonglistResponse(BaseModel):
    """Response schema for songlist details."""
    
    id: int = Field(description="Database ID")
    platform: Optional[str] = Field(description="Platform identifier")
    platform_songlist_id: Optional[str] = Field(description="Platform-specific ID")
    title: Optional[str] = Field(description="Songlist title")
    creator_name: Optional[str] = Field(description="Creator name")
    cover_url: Optional[str] = Field(description="Cover URL")
    song_count: int = Field(description="Number of songs in this list")
    metadata_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="Additional metadata"
    )

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": 1,
                "platform": "qqmusic",
                "platform_songlist_id": "123456789",
                "title": "My Favorite Songs",
                "creator_name": "John Doe",
                "cover_url": "https://example.com/cover.jpg",
                "song_count": 50,
                "metadata_json": None
            }
        }
