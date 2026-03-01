from time import time
from utils.enumerations import EventType
from typing import Literal, Any, List, Dict
from enum import Enum
from pydantic import BaseModel, Field

from utils.enumerations import GameEventType
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


class AttemptAnswerData(BaseModel):
    offset_ts: int = Field(..., ge=0)
    user_id: int = Field(..., ge=0)


class AttemptAnswerMessage(MessageBase):
    event: Literal[33] = GameEventType.ATTEMPT_ANSWER.value
    data: AttemptAnswerData


class YourTurnData(BaseModel):
    """你的回合数据"""
    pass  # 可能不需要额外数据，但保留结构


class YourTurnMessage(MessageBase):
    event: Literal[34] = GameEventType.YOUR_TURN.value
    data: YourTurnData


class SubmitAnswerData(BaseModel):
    """提交答案数据"""
    selected_tag_ids: List[int] = Field(default_factory=list,
                                        description="选择的标签ID列表")
    description_text: str | None = Field(default=None, description="精准描述文本")


class SubmitAnswerMessage(MessageBase):
    event: Literal[35] = GameEventType.SUBMIT_ANSWER.value
    data: SubmitAnswerData


class AnswerBroadcastData(BaseModel):
    """答案广播数据"""
    # 可以匿名化，或者包含玩家ID
    player_id: str = Field(..., description="玩家ID")
    selected_tag_ids: List[int] = Field(default_factory=list,
                                        description="选择的标签ID列表")
    description_text: str | None = Field(default=None, description="精准描述文本")


class AnswerBroadcastMessage(MessageBase):
    event: Literal[36] = GameEventType.ANSWER_BROADCAST.value
    data: AnswerBroadcastData


class ErrorMessageData(AutoEventConvertMixin):
    message: str = Field(..., description="错误消息内容")
    error_event: int | EventType | GameEventType = Field(
        ..., description="引发错误的事件类型")


class ErrorMessage(MessageBase, AutoEventConvertMixin):
    event: Literal[255] = EventType.ERROR.value
    data: ErrorMessageData = Field(..., description="错误消息数据")
