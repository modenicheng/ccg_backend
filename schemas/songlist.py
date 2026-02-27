"""
Songlist request/response schemas.

Handles:
- Songlist import/creation requests
- Songlist fetch operations
- Songlist API responses
"""

from datetime import datetime
from typing import Any, Optional, List
from pydantic import BaseModel, ConfigDict, Field

from utils.enumerations import MusicPlatform
from schemas.song import SongResponse


class SonglistBase(BaseModel):
    """Base schema for songlist data."""

    platform: Optional[str] = Field(
        default=None,
        description="Platform identifier (e.g., 'qqmusic', 'netease')")
    platform_songlist_id: Optional[str] = Field(
        default=None, description="Platform-specific songlist ID")
    title: Optional[str] = Field(default=None, description="Songlist title")
    creator_name: Optional[str] = Field(default=None,
                                        description="Creator/author name")
    cover_url: Optional[str] = Field(default=None,
                                     description="Cover image URL")
    # metadata_json: Optional[dict[str, Any]] = Field(
    #     default=None, description="Additional metadata as JSON")

    model_config = ConfigDict(from_attributes=True)


class SonglistCreate(SonglistBase):
    """Schema for creating a new songlist."""
    pass


class SonglistFetchRequest(BaseModel):
    """Request schema for fetching a songlist from a platform."""

    platform_songlist_id: str = Field(
        ...,
        description=
        "Platform-specific songlist ID (e.g., '123456789' for QQ Music)")
    platform: str = Field(
        default="qqmusic",
        description="Platform identifier (e.g., 'qqmusic', 'netease')")
    max_retries: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional override for max retry attempts")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "platform_songlist_id": "123456789",
                "platform": "qqmusic",
                "max_retries": 5
            }
        })


class SonglistCreateRequest(BaseModel):
    """Request schema for creating/importing a songlist."""

    platform: str = Field(description="Platform identifier")
    platform_songlist_id: str = Field(
        description="Platform-specific songlist ID")
    title: Optional[str] = Field(default=None, description="Songlist title")
    creator_name: Optional[str] = Field(default=None,
                                        description="Creator/author name")
    cover_url: Optional[str] = Field(default=None,
                                     description="Cover image URL")
    metadata_json: Optional[dict[str, Any]] = Field(
        default=None, description="Additional metadata as JSON")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "platform": "qqmusic",
                "platform_songlist_id": "123456789",
                "title": "My Favorite Songs",
                "creator_name": "John Doe",
                "cover_url": "https://example.com/cover.jpg",
                "metadata_json": {
                    "description": "A collection of classics"
                }
            }
        })


class SonglistResponse(SonglistBase):
    """Response schema for songlist details."""

    id: int = Field(description="Database ID")
    cover_url: Optional[str] = Field(default=None,
                                     description="Cover image URL")
    count: int = Field(description="Number of songs in this list")
    songs: Optional[List[SongResponse]] = Field(
        default=None,
        description=
        "List of songs in this songlist (optional, only included in detail view)"
    )

    model_config = ConfigDict(from_attributes=True,
                              json_schema_extra={
                                  "example": {
                                      "id": 1,
                                      "platform": "qqmusic",
                                      "platform_songlist_id": "123456789",
                                      "title": "My Favorite Songs",
                                      "creator_name": "John Doe",
                                      "cover_url":
                                      "https://example.com/cover.jpg",
                                      "count": 50,
                                      "songs": None,
                                      "metadata_json": None
                                  }
                              })


class SonglistListResponse(BaseModel):
    """Response schema for a list of songlists."""

    total: int = Field(description="Total number of songlists available")
    list: List[SonglistResponse] = Field(
        description="List of songlist details")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total":
                2,
                "list": [{
                    "id": 1,
                    "platform": "qqmusic",
                    "platform_songlist_id": "123456789",
                    "title": "My Favorite Songs",
                    "creator_name": "John Doe",
                    "cover_url": "https://example.com/cover.jpg",
                    "count": 50,
                    "metadata_json": None
                }, {
                    "id": 2,
                    "platform": "netease",
                    "platform_songlist_id": "987654321",
                    "title": "Chill Vibes",
                    "creator_name": "Jane Smith",
                    "cover_url": None,
                    "count": 30,
                    "metadata_json": {
                        "description": "Relaxing tunes"
                    }
                }]
            }
        })


class SonglistFromMidRequest(BaseModel):
    """Request schema for creating a songlist from a platform-specific ID."""

    platform: MusicPlatform = Field(description="Platform identifier")
    platform_songlist_id: str = Field(
        description="Platform-specific songlist ID")
    cookie_str: Optional[str] = Field(
        default=None,
        description="Optional cookie string for authenticated requests")
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "platform": "qq",
            "platform_songlist_id": "9561074811"
        }
    })
