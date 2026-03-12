"""
公共Schema定义
包含多个模块共享的Pydantic模型
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RoomStateTagItem(BaseModel):
    """房间状态标签项"""

    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class RoomStateTagGroupItem(BaseModel):
    """房间状态标签组项"""

    id: int
    name: str
    description: str | None = None
    tags: list[RoomStateTagItem] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class RoomStatePlayerItem(BaseModel):
    """房间状态玩家项"""

    id: int
    username: str
    is_owner: bool
    online: bool = True

    model_config = ConfigDict(from_attributes=True)


# 示例数据常量
SONG_EXAMPLE = {
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
    "metadata_json": None,
}
