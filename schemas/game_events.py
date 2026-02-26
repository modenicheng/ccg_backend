from time import time
from typing import Literal

from pydantic import BaseModel, Field

from utils.enumerations import GameEventType


class PlayControlData(BaseModel):
    progress_ms: int = Field(default=0, ge=0)
    offset_ts: int = Field(..., ge=0)
    audio_url: str | None = Field(default=None)


class SeekMessage(BaseModel):
    event: Literal[22] = GameEventType.SEEK.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: PlayControlData


class PlayMessage(BaseModel):
    event: Literal[20] = GameEventType.PLAY.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: PlayControlData


class PauseMessage(BaseModel):
    event: Literal[21] = GameEventType.PAUSE.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: PlayControlData
