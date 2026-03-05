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
    platform_url: str | None = None


class PlayerDescription(BaseModel):
    id: int
    username: str
    description: str


class JudgingData(BaseModel):
    song: SongInfo
    history_tag_ids: list[int] = Field(default_factory=list)
    reference_descriptions: list[str] = Field(default_factory=list)
    player_descriptions: list[PlayerDescription] = Field(default_factory=list)


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
    
class SkipRoundMessage(MessageBase):
    event: Literal[43] = GameEventType.SKIP_ROUND.value
    data: Any = None  # 可以根据需要添加字段

class ShowAnswerData(BaseModel):
    tag_ids: list[int] = Field(default_factory=list, description="正确标签ID列表")
    description_ids: list[int] = Field(default_factory=list, description="正确描述ID列表")

class ShowAnswerMessage(MessageBase):
    event: Literal[44] = GameEventType.SHOW_ANSWER.value
    data: ShowAnswerData