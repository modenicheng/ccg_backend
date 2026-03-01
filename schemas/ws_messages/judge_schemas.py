from time import time
from typing import Literal, Any, List
from pydantic import BaseModel, Field
from utils.enumerations import GameEventType
from ..base_message import MessageBase


class SongInfo(BaseModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    cover_url: str | None = None


class JudgingData(BaseModel):
    song: SongInfo


class JudgingMessage(MessageBase):
    event: Literal[40] = GameEventType.JUDGING.value
    data: JudgingData


class JudgeSubmitData(BaseModel):
    correct_tags: list[int] = Field(default_factory=list)
    correct_description_ids: list[int] = Field(default_factory=list)
    new_correct_descriptions: list[str] = Field(default_factory=list)
    skip_scoring: bool = False


class JudgeSubmitMessage(MessageBase):
    event: Literal[41] = GameEventType.JUDGE_SUBMIT.value
    data: JudgeSubmitData


class ScoreEntry(BaseModel):
    """分数条目"""
    player_id: str = Field(..., description="玩家ID")
    score: int = Field(..., description="分数值")
    username: str | None = Field(default=None, description="玩家用户名")


class ScoreUpdateData(BaseModel):
    scores: list[ScoreEntry] = Field(default_factory=list)


class ScoreUpdateMessage(MessageBase):
    event: Literal[42] = GameEventType.SCORE_UPDATE.value
    data: ScoreUpdateData