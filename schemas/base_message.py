from time import time
from utils.enumerations import EventType
from typing import Literal, Any, List, Dict

from pydantic import BaseModel, Field

from utils.enumerations import GameEventType
from utils.ts import get_ts_ms


class MessageBase(BaseModel):
    event: Any = Field(..., description="事件类型")
    ts: int = Field(default_factory=get_ts_ms, description="事件发生的时间戳（毫秒）")


class AttemptAnswerData(BaseModel):
    offset_ts: int = Field(..., ge=0)
    user_id: int = Field(..., ge=0)


class AttemptAnswerMessage(MessageBase):
    event: Literal[33] = GameEventType.ATTEMPT_ANSWER.value
    data: AttemptAnswerData


class AnswerQueueEntry(BaseModel):
    """抢答队列条目"""
    player_id: str = Field(..., description="玩家ID")
    offset_ts: int = Field(..., ge=0, description="客户端校准时间戳")
    server_ts: int = Field(..., ge=0, description="服务器接收时间戳")
    added_at: str = Field(..., description="加入队列时间")


class AnswerQueueData(BaseModel):
    """抢答队列数据"""
    queue: List[AnswerQueueEntry] = Field(default_factory=list,
                                          description="抢答队列")


class AnswerQueueMessage(MessageBase):
    event: Literal[37] = GameEventType.ANSWER_QUEUE.value
    data: AnswerQueueData


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


class ErrorMessageData(BaseModel):
    message: str = Field(..., description="错误消息内容")
    error_event: EventType | GameEventType = Field(...,
                                                   description="引发错误的事件类型")


class ErrorMessage(MessageBase):
    event: Literal[255] = EventType.ERROR.value
    data: ErrorMessageData = Field(..., description="错误消息数据")
