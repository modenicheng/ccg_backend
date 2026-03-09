from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from time import time
from schemas.base_message import MessageBase
from utils.enumerations import GameEventType
from db.models import RoomStatusORM
from schemas.common import RoomStateTagItem, RoomStateTagGroupItem, RoomStatePlayerItem


class AnswerQueueItem(BaseModel):
    player_id: int
    order: int | None = None
    offset_ts: int = Field(default=0, ge=0)  # 玩家答题时的时间戳（毫秒），经过前端修正
    # 服务器记录的时间戳（毫秒），如果offset_ts一致，则用这个字段来判断先后顺序
    server_ts: int = Field(default_factory=lambda: int(time() * 1000))
    is_answering: bool = False


class PlaybackState(BaseModel):
    progress_ms: int = Field(default=0, ge=0)
    # 这个字段主要是为了让前端知道状态更新时间，方便算播放进度
    updated_at: int = Field(default_factory=lambda: int(time() * 1000))
    offset_ts: int = Field(default=0, ge=0, description="前端经过修正的时间戳, ms")  # 前端经过修正的时间戳
    play_state: Literal["playing", "paused"] = Field(default="paused")
    current_order: int = Field(default=0, ge=0)
    audio_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ScoreItem(BaseModel):
    player_id: int = Field(..., description="玩家ID", alias="user_id")
    round_index: int
    score_delta: int
    total_score: int

    model_config = ConfigDict(from_attributes=True)


class ClientRoomState(BaseModel):
    room_id: str = Field(alias="id")
    title: str | None = None
    status: RoomStatusORM = RoomStatusORM.WAITING
    round_state: Literal[0, 1, 2, 3, 4] = (
        0  # 0=PENDING, 1=PLAYING_AUDIO, 2=ANSWERING, 3=JUDGING, 4=COMPLETED
    )
    song_start_range_percent: float | None = Field(default=0, ge=0, le=100)
    players: list[RoomStatePlayerItem] = Field(default_factory=list, alias="users")
    tag_groups: list[RoomStateTagGroupItem] = Field(default_factory=list)
    answer_queue: list[AnswerQueueItem] = Field(default_factory=list)
    playback_status: PlaybackState | None = Field(default=None, description="当前播放状态")
    scores: list[ScoreItem] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# This is the "full" room state that includes all details, used for debugging or when a client needs the complete state. The ClientRoomState is what is typically sent to clients, and it can be extended with additional fields if needed without affecting the core structure.
# 所以这个模型用于服务端自身状态维护，这东西不要发给客户端
class FullRoomState(ClientRoomState):
    song_queue: list[int] = Field(default_factory=list)


class RoomStateMessage(BaseModel):
    event: Literal[12] = GameEventType.ROOM_STATE.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: ClientRoomState

    model_config = ConfigDict(from_attributes=True)


class PlayerJoinMessage(BaseModel):
    event: Literal[11] = GameEventType.ROOM_JOIN.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: RoomStatePlayerItem

    model_config = ConfigDict(from_attributes=True)


class PlayerLeaveMessage(MessageBase):
    event: Literal[16] = GameEventType.PLAYER_LEAVE.value
    data: RoomStatePlayerItem

    model_config = ConfigDict(from_attributes=True)


class KickUserMessage(BaseModel):
    event: Literal[15] = GameEventType.KICK_USER.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: dict


class StartPosUpdateData(BaseModel):
    start_position_percent: float = Field(..., ge=0, le=80, description="起始位置百分比")


class StartPosUpdateMessage(BaseModel):
    event: Literal[14] = GameEventType.START_POS_UPDATE.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: StartPosUpdateData


class GameOverScore(BaseModel):
    player_id: int
    username: str
    score: int


class GameOverData(BaseModel):
    manual: bool = Field(default=False, description="是否手动结束游戏")
    final_scores: list[GameOverScore] = Field(default_factory=list, description="最终得分")


class GameOverMessage(BaseModel):
    event: Literal[13] = GameEventType.GAME_OVER.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: GameOverData


class ClearAnswerQueueData(BaseModel):
    pass


class ClearAnswerQueueMessage(BaseModel):
    event: Literal[53] = GameEventType.CLEAR_ANSWER_QUEUE.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: ClearAnswerQueueData
