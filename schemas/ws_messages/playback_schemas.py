"""Playback WebSocket message schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from utils.enumerations import GameEventType
from ..base_message import MessageBase


class PlayControlData(BaseModel):
    progress_ms: int = Field(default=0, ge=0)
    offset_ts: int | None = Field(default=None, ge=0, description="前端经过修正的时间戳，后端不用填入")
    audio_url: str | None = Field(default=None)
    current_order: int = Field(
        default=0,
        ge=-1,
        description="当前曲目序号，-1 表示 test audio。兼容旧客户端默认 0",
    )


class PlayMessage(MessageBase):
    event: Literal[20] = GameEventType.PLAY.value
    data: PlayControlData


class PauseMessage(MessageBase):
    event: Literal[21] = GameEventType.PAUSE.value
    data: PlayControlData


class SeekMessage(MessageBase):
    event: Literal[22] = GameEventType.SEEK.value
    data: PlayControlData


class PreloadAudioData(BaseModel):
    """Browser preload target for the next song."""

    audio_url: str


class PreloadAudioMessage(MessageBase):
    """Server-owned instruction to preload the next song."""

    event: Literal[23] = GameEventType.PRELOAD_AUDIO.value
    data: PreloadAudioData
