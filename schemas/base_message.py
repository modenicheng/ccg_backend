from __future__ import annotations
from time import time
from utils.enumerations import EventType, GameEventType
from typing import Literal, Any, List, Dict
from enum import Enum
from pydantic import BaseModel, Field

from utils.ts import get_ts_ms


class MessageBase(BaseModel):
    event: Any = Field(..., description="事件类型")
    ts: int = Field(default_factory=get_ts_ms, description="事件发生的时间戳（毫秒）")


class AutoEventConvertMixin(BaseModel):

    def model_dump(self, *args, **kwargs) -> dict:
        """
        重写 model_dump 方法，自动将事件类型枚举转换为对应的值。
        这样在创建消息对象时可以直接使用枚举类型，调用 model_dump 时会自动转换。
        """
        data = super().model_dump(*args, **kwargs)
        event = data.get("event")
        if isinstance(event, Enum):
            data["event"] = event.value
        return data


class ErrorMessageData(AutoEventConvertMixin):
    message: str = Field(..., description="错误消息内容")
    error_event: int | EventType | GameEventType = Field(
        ..., description="引发错误的事件类型")


class ErrorMessage(MessageBase, AutoEventConvertMixin):
    event: Literal[255] = EventType.ERROR.value
    data: ErrorMessageData = Field(..., description="错误消息数据")
