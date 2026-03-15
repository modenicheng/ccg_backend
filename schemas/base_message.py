"""Base message schemas for WebSocket communication."""

from __future__ import annotations
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from utils.enumerations import EventType, GameEventType
from utils.ts import get_ts_ms


class MessageBase(BaseModel):
    """Base class for all WebSocket messages."""

    event: Any = Field(..., description="事件类型")
    ts: int = Field(default_factory=get_ts_ms, description="事件发生的时间戳（毫秒）")


class AutoEventConvertMixin(BaseModel):
    """Mixin to automatically convert event enums to values during serialization."""

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
    """Data payload for error messages."""

    message: str = Field(..., description="错误消息内容")
    error_event: int | EventType | GameEventType = Field(..., description="引发错误的事件类型")


class ErrorMessage(MessageBase, AutoEventConvertMixin):
    """Error message schema."""

    event: Literal[255] = Field(default=255, description="错误事件类型，固定为255")
    data: ErrorMessageData = Field(..., description="错误消息数据")
