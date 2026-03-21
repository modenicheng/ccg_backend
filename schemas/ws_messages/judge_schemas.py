"""
Schemas for WebSocket messages related to judge submissions.

This module defines data models used for validating and serializing data
exchanged during judge submission events over WebSocket connections.

Classes:
    JudgeSubmitData: Data model representing a judge's submission, including
        correct tags, description IDs, new descriptions, and scoring options.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field
from utils.enumerations import GameEventType
from ..base_message import MessageBase


class SongInfo(BaseModel):
    """
    SongInfo contains metadata about a song.

    Attributes:
        title (Optional[str]): The title of the song.
        artist (Optional[str]): The artist of the song.
        album (Optional[str]): The album name.
        cover_url (Optional[str]): The URL to the cover image.
        platform_url (Optional[str]): The URL to the song on the platform.
    """
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    cover_url: str | None = None
    platform_url: str | None = None


class TagData(BaseModel):
    """标签数据"""
    id: int = Field(..., description="标签ID")
    name: str = Field(..., description="标签名称")


class TagGroupData(BaseModel):
    """标签组数据"""
    group_id: int = Field(..., description="标签组ID")
    name: str = Field(..., description="标签组名称")
    tags: list[TagData] = Field(default_factory=list, description="标签列表")


class DescriptionCandidate(BaseModel):
    """精准描述候选"""
    id: int = Field(..., description="描述ID")
    text: str = Field(..., description="描述文本")
    count: int = Field(default=0, description="历史上被选为正确的次数")


class PlayerAnswerData(BaseModel):
    """玩家答案数据"""
    player_id: int = Field(..., description="玩家ID")
    username: str = Field(..., description="玩家用户名")
    selected_tags: list[int] = Field(default_factory=list, description="选择的标签ID列表")
    description: str | None = Field(default=None, description="精准描述文本")


class JudgingData(BaseModel):
    """
    JudgingData contains all information needed for the judge to review a round.

    Attributes:
        tag_groups (list[TagGroupData]): 房间的标签组列表
        description_candidates (list[DescriptionCandidate]): 精准描述候选列表
        answers (list[PlayerAnswerData]): 本轮所有玩家提交的答案
    """
    tag_groups: list[TagGroupData] = Field(default_factory=list)
    description_candidates: list[DescriptionCandidate] = Field(default_factory=list)
    answers: list[PlayerAnswerData] = Field(default_factory=list)


class JudgingMessage(MessageBase):
    """
    WebSocket message schema for sending judging data to the judge.

    Contains the event type and the data required for the judge to review a round.
    """
    event: int = GameEventType.JUDGING.value
    data: JudgingData


class JudgeSubmitData(BaseModel):
    """
    Data model for judge submission data sent via WebSocket.

    Attributes:
        correct_tags: List of IDs representing correct tags selected by the judge.
        correct_description_ids: List of IDs for descriptions marked as correct.
        new_correct_descriptions: List of new correct descriptions provided.
        skip_scoring: Flag indicating whether to skip scoring for this submission.
    """
    correct_tags: list[int] = Field(default_factory=list)
    correct_description_ids: list[int] = Field(default_factory=list)
    new_correct_descriptions: list[str] = Field(default_factory=list)
    skip_scoring: bool = False


class JudgeSubmitMessage(MessageBase):
    """
    WebSocket message schema for judge submission events.

    Contains the event type and the judge's submission data.
    """
    event: int = GameEventType.JUDGE_SUBMIT.value
    data: JudgeSubmitData


class ScoreEntry(BaseModel):
    """
    ScoreEntry represents a single score record for a player.

    Attributes:
        player_id (str): 玩家ID (Player ID).
        score (int): 分数值 (Score value).
        username (Optional[str]): 玩家用户名 (Player username), optional.
    """
    player_id: str = Field(..., description="玩家ID")
    score: int = Field(..., description="分数值")
    username: str | None = Field(default=None, description="玩家用户名")


class ScoreUpdateData(BaseModel):
    """
    ScoreUpdateData contains a list of score entries for players.

    Attributes:
        scores (list[ScoreEntry]): List of player score entries.
    """
    scores: list[ScoreEntry] = Field(default_factory=list)


class ScoreUpdateMessage(MessageBase):
    """
    WebSocket message schema for updating player scores.

    Contains the event type and the updated score data.
    """
    event: int = GameEventType.SCORE_UPDATE.value
    data: ScoreUpdateData


class SkipRoundMessage(MessageBase):
    """
    WebSocket message schema for skipping the current round.

    Contains the event type and optional data for skipping a round.
    """
    event: int = GameEventType.SKIP_ROUND.value
    data: Any = None


class ShowAnswerData(BaseModel):
    """
    ShowAnswerData contains the correct tag and description IDs for the answer.

    Attributes:
        tag_ids (list[int]): 正确标签ID列表 (List of correct tag IDs).
        description_ids (list[int]): 正确描述ID列表 (List of correct description IDs).
    """
    tag_ids: list[int] = Field(default_factory=list, description="正确标签ID列表")
    description_ids: list[int] = Field(default_factory=list, description="正确描述ID列表")


class ShowAnswerMessage(MessageBase):
    """
    WebSocket message schema for showing the correct answer.

    Contains the event type and the correct answer data.
    """
    event: int = GameEventType.SHOW_ANSWER.value
    data: ShowAnswerData
