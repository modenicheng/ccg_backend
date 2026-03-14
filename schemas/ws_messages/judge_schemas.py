"""
Schemas for WebSocket messages related to judge submissions.

This module defines data models used for validating and serializing data
exchanged during judge submission events over WebSocket connections.

Classes:
    JudgeSubmitData: Data model representing a judge's submission, including
        correct tags, description IDs, new descriptions, and scoring options.
"""
from __future__ import annotations
from typing import Literal, Any
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


class PlayerDescription(BaseModel):
    """
    PlayerDescription represents a player's submitted description.

    Attributes:
        id (int): The unique identifier for the player's description.
        username (str): The player's username.
        description (str): The description text submitted by the player.
    """
    id: int
    username: str
    description: str


class JudgingData(BaseModel):
    """
    JudgingData contains all information needed for the judge to review a round.

    Attributes:
        song (SongInfo): Information about the current song.
        history_tag_ids (list[int]): List of previously used tag IDs.
        reference_descriptions (list[str]): Reference descriptions for the song.
        player_descriptions (list[PlayerDescription]): Descriptions submitted by players.
    """
    song: SongInfo
    history_tag_ids: list[int] = Field(default_factory=list)
    reference_descriptions: list[str] = Field(default_factory=list)
    player_descriptions: list[PlayerDescription] = Field(default_factory=list)


class JudgingMessage(MessageBase):
    """
    WebSocket message schema for sending judging data to the judge.

    Contains the event type and the data required for the judge to review a round.
    """
    event: Literal[40] = GameEventType.JUDGING.value
    data: JudgingData


class JudgeSubmitData(BaseModel):
    """
    Data model for judge submission data sent via WebSocket.

    Attributes:
        correct_tags (list[int]): List of IDs representing correct tags selected by the judge.
        correct_description_ids (list[int]): List of IDs for descriptions marked as correct.
        new_correct_descriptions (list[str]): List of new correct descriptions provided by the judge.
        skip_scoring (bool): Flag indicating whether to skip scoring for this submission.
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
    event: Literal[41] = GameEventType.JUDGE_SUBMIT.value
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
    event: Literal[42] = GameEventType.SCORE_UPDATE.value
    data: ScoreUpdateData


class SkipRoundMessage(MessageBase):
    """
    WebSocket message schema for skipping the current round.

    Contains the event type and optional data for skipping a round.
    """
    event: Literal[43] = GameEventType.SKIP_ROUND.value
    data: Any = None  # 可以根据需要添加字段


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
    event: Literal[44] = GameEventType.SHOW_ANSWER.value
    data: ShowAnswerData
