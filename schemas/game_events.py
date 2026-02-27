from time import time
from typing import Literal, Any

from pydantic import BaseModel, Field

from utils.enumerations import GameEventType


class PlayControlData(BaseModel):
    progress_ms: int = Field(default=0, ge=0)
    offset_ts: int = Field(..., ge=0)
    audio_url: str | None = Field(default=None)


class SeekMessage(BaseModel):
    event: Literal[22] = GameEventType.SEEK.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: PlayControlData


class PlayMessage(BaseModel):
    event: Literal[20] = GameEventType.PLAY.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: PlayControlData


class PauseMessage(BaseModel):
    event: Literal[21] = GameEventType.PAUSE.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: PlayControlData


class SongInfo(BaseModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    cover_url: str | None = None


class JudgingData(BaseModel):
    song: SongInfo


class JudgingMessage(BaseModel):
    event: Literal[40] = GameEventType.JUDGING.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: JudgingData


class JudgeSubmitData(BaseModel):
    correct_tags: list[int] = Field(default_factory=list)
    correct_description_ids: list[int] = Field(default_factory=list)
    new_correct_descriptions: list[str] = Field(default_factory=list)
    skip_scoring: bool = False


class JudgeSubmitMessage(BaseModel):
    event: Literal[41] = GameEventType.JUDGE_SUBMIT.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: JudgeSubmitData


class ScoreUpdateData(BaseModel):
    scores: list[dict[str, Any]]


class ScoreUpdateMessage(BaseModel):
    event: Literal[42] = GameEventType.SCORE_UPDATE.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: ScoreUpdateData

class AttemptAnswerData(BaseModel):
    offset_ts: int = Field(..., ge=0)
    user_id: int = Field(..., ge=0)

class AttemptAnswerMessage(BaseModel):
    event: Literal[33] = GameEventType.ATTEMPT_ANSWER.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: AttemptAnswerData
