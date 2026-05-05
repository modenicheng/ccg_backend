from __future__ import annotations
from .room_schemas import AnswerQueueItem

from ..base_message import MessageBase
from pydantic import BaseModel, Field
from typing import Literal, List
from utils.enumerations import GameEventType


class GameStartData(BaseModel):
    """游戏开始数据"""


class GameStartMessage(MessageBase):
    event: Literal[31] = GameEventType.GAME_START.value
    data: GameStartData


class RoundStartData(BaseModel):
    """回合开始数据"""
    round_index: int = Field(..., ge=0)
    audio_url: str | None = None
    start_percent: float = Field(default=0.0, ge=0.0, le=1.0)


class RoundStartMessage(MessageBase):
    event: Literal[32] = GameEventType.ROUND_START.value
    data: RoundStartData


class RoundEndData(BaseModel):
    """回合结束数据"""


class AnswerQueueData(BaseModel):
    """抢答队列数据"""
    queue: List[AnswerQueueItem] = Field(default_factory=list, description="抢答队列")
    answer_queue_tail_player_id: int | None = Field(
        default=None,
        description="当前回合已处理段队尾玩家ID（用于前端边界控制）",
    )


class AnswerQueueMessage(MessageBase):
    event: Literal[37] = GameEventType.ANSWER_QUEUE.value
    data: AnswerQueueData


class RoundEndMessage(MessageBase):
    event: Literal[38] = GameEventType.ROUND_END.value
    data: RoundEndData = Field(default_factory=RoundEndData)


class AttemptAnswerData(BaseModel):
    offset_ts: int = Field(..., ge=0)
    progress_ms: int = Field(..., ge=0)
    user_id: int = Field(..., ge=0)
    queue: List[AnswerQueueItem] | None = Field(
        default=None,
        description="抢答队列（可选，用于前端队列同步）",
    )
    answer_queue_tail_player_id: int | None = Field(
        default=None,
        description="当前回合已处理段队尾玩家ID（可选，用于前端队列同步）",
    )


class AttemptAnswerMessage(MessageBase):
    event: Literal[33] = GameEventType.ATTEMPT_ANSWER.value
    data: AttemptAnswerData


class YourTurnData(BaseModel):
    """你的回合数据"""
    user_id: int = Field(..., ge=0, description="当前轮到作答的玩家ID")
    answer_deadline: int = Field(default=0, ge=0, description="作答截止时间（服务器时间戳，毫秒）")


class YourTurnMessage(MessageBase):
    event: Literal[34] = GameEventType.YOUR_TURN.value
    data: YourTurnData


class SubmitAnswerData(BaseModel):
    """提交答案数据"""
    selected_tag_ids: List[int] = Field(default_factory=list, description="选择的标签ID列表")
    description_text: str | None = Field(default=None, description="精准描述文本")


class SubmitAnswerMessage(MessageBase):
    event: Literal[35] = GameEventType.SUBMIT_ANSWER.value
    data: SubmitAnswerData


class AnswerBroadcastData(BaseModel):
    """答案广播数据"""
    player_id: int = Field(..., description="玩家ID")
    selected_tag_ids: List[int] = Field(default_factory=list, description="选择的标签ID列表")
    description_text: str | None = Field(default=None, description="精准描述文本")


class AnswerBroadcastMessage(MessageBase):
    event: Literal[36] = GameEventType.ANSWER_BROADCAST.value
    data: AnswerBroadcastData
