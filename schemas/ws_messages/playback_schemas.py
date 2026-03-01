from pydantic import BaseModel, Field
from typing import Literal
from utils.enumerations import RoomStatus, GameEventType
from ..base_message import MessageBase


class PlayControlData(BaseModel):
    progress_ms: int = Field(default=0, ge=0)
    offset_ts: int | None = Field(default=None,
                                  ge=0,
                                  description="前端经过修正的时间戳，后端不用填入")
    audio_url: str | None = Field(default=None)


class SeekMessage(MessageBase):
    event: Literal[22] = GameEventType.SEEK.value
    data: PlayControlData


class PlayMessage(MessageBase):
    event: Literal[20] = GameEventType.PLAY.value
    data: PlayControlData


class PauseMessage(MessageBase):
    event: Literal[21] = GameEventType.PAUSE.value
    data: PlayControlData
