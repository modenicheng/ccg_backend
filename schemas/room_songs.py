from __future__ import annotations
"""
Room songs request/response schemas.

Handles:
- Room song association management
- Room song ordering
- Room song batch operations
"""

from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field

from schemas.song import SongResponse
from .common import SONG_EXAMPLE


class RoomSongBase(BaseModel):
    """Base schema for room song association data."""

    room_id: str = Field(description="Room ID")
    song_id: int = Field(description="Song ID")
    song_order: Optional[int] = Field(
        default=None,
        description="Song order in room queue (null means no specific order)",
    )

    model_config = ConfigDict(from_attributes=True)


class RoomSongResponse(RoomSongBase):
    """Response schema for room song association details."""

    song: SongResponse = Field(description="Song details")



class RoomSongsListResponse(BaseModel):
    """Response schema for a list of room songs."""

    room_id: str = Field(description="Room ID")
    list: List[RoomSongResponse] = Field(description="List of songs in this room")
    total: int | None = Field(description="Total number of songs in room", default=0)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "room_id":
                    "ABCD12",
                "total":
                    2,
                "list": [
                    {
                        "room_id": "ABCD12",
                        "song_id": 1,
                        "song_order": 1,
                        "song": {
                            "id": 1,
                            "platform": "qqmusic",
                            "platform_song_id": "001",
                            "title": "Song 1",
                            "artist": "Artist 1",
                            "cover_url": "https://example.com/cover1.jpg",
                            "cached": True,
                        },
                    },
                    {
                        "room_id": "ABCD12",
                        "song_id": 2,
                        "song_order": 2,
                        "song": {
                            "id": 2,
                            "platform": "qqmusic",
                            "platform_song_id": "002",
                            "title": "Song 2",
                            "artist": "Artist 2",
                            "cover_url": "https://example.com/cover2.jpg",
                            "cached": True,
                        },
                    },
                ],
            }
        })


class AddRoomSongsRequest(BaseModel):
    """Request schema for adding songs to a room."""

    song_ids: List[int] = Field(description="List of song IDs to add to the room")
    append_to_end: bool = Field(
        default=True,
        description=
        "If true, append songs to the end of queue; if false, insert at beginning",
    )

    model_config = ConfigDict(
        json_schema_extra={"example": {
            "song_ids": [1, 2, 3],
            "append_to_end": True
        }})


class RemoveRoomSongsRequest(BaseModel):
    """Request schema for removing songs from a room."""

    song_ids: List[int] = Field(description="List of song IDs to remove from the room")

    model_config = ConfigDict(json_schema_extra={"example": {"song_ids": [1, 2]}})


class UpdateRoomSongOrderRequest(BaseModel):
    """Request schema for updating song order in a room."""

    song_id: int = Field(description="Song ID to update order for")
    new_order: Optional[int] = Field(
        description="New order position (null to remove specific order)")

    model_config = ConfigDict(
        json_schema_extra={"example": {
            "song_id": 1,
            "new_order": 3
        }})


class BatchUpdateRoomSongOrderRequest(BaseModel):
    """Request schema for batch updating song orders in a room."""

    orders: List[UpdateRoomSongOrderRequest] = Field(
        description="List of song order updates")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "orders": [
                    {
                        "song_id": 1,
                        "new_order": 3
                    },
                    {
                        "song_id": 2,
                        "new_order": 1
                    },
                ]
            }
        })
