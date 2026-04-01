"""The schemas class of WebSocket messages related to answer queue."""
from __future__ import annotations
from pydantic import BaseModel, Field
from ..base_message import MessageBase


class SelectionData(BaseModel):
    """选择数据"""
    selected_tag_ids: list[int] = Field(default_factory=list)


class DescriptionData(BaseModel):
    """描述数据"""
    description_text: str = Field(..., description="玩家输入的描述文本")


class AnswerItem(SelectionData, DescriptionData):
    """抢答队列条目"""
    player_id: int = Field(..., description="玩家ID")


class AnswerFullMessage(MessageBase):
    """完整的抢答消息，包含玩家ID、选择和描述数据"""
    event: int = Field(..., description="事件类型")
    data: AnswerItem


class SelectionUpdateMessage(MessageBase):
    """选择数据的更新消息"""
    event: int = Field(..., description="事件类型")
    data: SelectionData


class DescriptionUpdateMessage(MessageBase):
    """描述数据的更新消息"""
    event: int = Field(..., description="事件类型")
    data: DescriptionData
