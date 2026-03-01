from ..base_message import MessageBase
from pydantic import BaseModel, Field
from typing import Literal, List
from utils.enumerations import GameEventType


class GameStartData(BaseModel):
    """游戏开始数据"""
    pass  # 可能不需要额外数据，但保留结构


class GameStartMessage(MessageBase):
    event: Literal[31] = GameEventType.GAME_START.value
    data: GameStartData


class RoundEndData(BaseModel):
    """回合结束数据"""
    pass  # 可能不需要额外数据，但保留结构


class RoundEndMessage(MessageBase):
    event: Literal[38] = GameEventType.ROUND_END.value
    data: RoundEndData
