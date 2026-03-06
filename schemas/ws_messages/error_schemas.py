from __future__ import annotations
from typing import Literal

from pydantic import BaseModel, Field

from utils.enumerations import EventType, GameEventType, ErrorEventType


class WebSocketErrorEvent(BaseModel):
    event: Literal[255] | EventType = Field(default=255, description="事件类型，固定为 255")
    error_event: EventType | GameEventType | ErrorEventType = Field(
        ..., description="引发错误的事件类型或错误类型")
    message: str = Field(..., description="错误消息内容")

    def model_dump(self, *args, **kwargs):
        # 重写 model_dump 方法，确保 event 字段始终输出为整数 255
        data = super().model_dump(*args, **kwargs)
        data["event"] = 255  # 强制设置 event 字段为 255
        data["error_event"] = self.error_event.value if isinstance(
            self.error_event,
            (EventType, GameEventType, ErrorEventType)) else self.error_event
        return data
