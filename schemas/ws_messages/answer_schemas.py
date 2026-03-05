from __future__ import annotations
from ..base_message import MessageBase
from pydantic import BaseModel, Field


class SelectionData(BaseModel):
    """选择数据"""
    selected_tag_ids: list[int] = Field(default_factory=list)


class DescriptionData(BaseModel):
    """描述数据"""
    description_text: str = Field(..., description="玩家输入的描述文本")


class AnswerItem(SelectionData, DescriptionData):
    """抢答队列条目"""
    player_id: str = Field(..., description="玩家ID")


class AnswerFullMessage(MessageBase):
    event: int = Field(..., description="事件类型")
    data: AnswerItem


class SelectionUpdateMessage(MessageBase):
    event: int = Field(..., description="事件类型")
    data: SelectionData


class DescriptionUpdateMessage(MessageBase):
    event: int = Field(..., description="事件类型")
    data: DescriptionData
