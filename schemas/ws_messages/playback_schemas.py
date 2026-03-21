from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal
from utils.enumerations import RoomStatus, GameEventType, EventType
from ..base_message import MessageBase


class PlayControlData(BaseModel):
    progress_ms: int = Field(default=0, ge=0)
    offset_ts: int | None = Field(default=None, ge=0, description="前端经过修正的时间戳，后端不用填入")
    audio_url: str | None = Field(default=None)


class PlayMessage(MessageBase):
    event: Literal[20] = GameEventType.PLAY.value
    data: PlayControlData


class PauseMessage(MessageBase):
    event: Literal[21] = GameEventType.PAUSE.value
    data: PlayControlData


class SeekMessage(MessageBase):
    event: Literal[22] = GameEventType.SEEK.value
    data: PlayControlData


class PreloadAudioMessage(MessageBase):
    event: Literal[23] = GameEventType.PRELOAD_AUDIO.value
    data: PlayControlData


class AudioErrorData(BaseModel):
    """前端音频错误报告数据"""
    error_type: Literal["load_failed", "sync_failed"] = Field(
        ..., description="错误类型：load_failed（加载失败）或 sync_failed（同步失败）")
    reason: str = Field(..., description="错误原因/详细信息")
    audio_url: str = Field(default="unknown",
                           description="当前尝试加载的音频URL，如果URL不可用则为'unknown'")


class AudioErrorMessage(MessageBase):
    """前端音频错误事件"""
    event: Literal[255] = EventType.ERROR.value
    data: AudioErrorData
