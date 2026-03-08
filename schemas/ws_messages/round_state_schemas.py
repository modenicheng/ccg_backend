from __future__ import annotations
from pydantic import BaseModel
from schemas.base_message import MessageBase


class RoundStateUpdateData(BaseModel):
    """回合状态更新数据"""
    round_state: int  # 0=PENDING, 1=PLAYING_AUDIO, 2=ANSWERING, 3=JUDGING, 4=COMPLETED
    round_state_name: str  # 状态名称，用于前端显示


class RoundStateUpdateMessage(MessageBase):
    """回合状态更新消息"""
    event: int = 45  # GameEventType.ROUND_STATE_UPDATE
    data: RoundStateUpdateData
