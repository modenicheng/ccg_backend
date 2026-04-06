from __future__ import annotations
from typing import Literal
from time import time

from pydantic import BaseModel, ConfigDict, Field

from utils.enumerations import GameEventType
from db.models import RoomStatusORM
from schemas.base_message import MessageBase
from schemas.common import (RoomStateTagGroupItem, RoomStatePlayerItem)
from schemas.tag import TagResponse, TagGroupResponse


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
    current_order: int = Field(default=0, ge=-1, description="当前曲目序号，-1 表示 test audio")
    audio_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ScoreItem(BaseModel):
    player_id: int = Field(..., description="玩家ID", alias="user_id")
    round_index: int
    score_delta: int
    total_score: int

    model_config = ConfigDict(from_attributes=True)


class RoundAnswerItem(BaseModel):
    player_id: int
    username: str
    answers: dict[int, int] = Field(default_factory=dict,
                                    description="tagGroupId -> tagId")
    description: str | None = None
    order: int = Field(default=0, ge=0)


class ClientRoomState(BaseModel):
    room_id: str = Field(alias="id")
    title: str | None = None
    status: RoomStatusORM = RoomStatusORM.WAITING
    round_state: Literal[0, 1, 2, 3, 4] = (
        0  # 0=PENDING, 1=PLAYING_AUDIO, 2=ANSWERING, 3=JUDGING, 4=COMPLETED
    )
    show_answer: bool = False
    song_start_range_percent: float | None = Field(default=0, ge=0, le=100)
    players: list[RoomStatePlayerItem] = Field(default_factory=list, alias="users")
    tag_groups: list[RoomStateTagGroupItem] = Field(default_factory=list)
    answer_queue: list[AnswerQueueItem] = Field(default_factory=list)
    answer_queue_tail_player_id: int | None = Field(
        default=None,
        description="当前回合已处理段队尾玩家ID（用于前端边界控制）",
    )
    round_scored: bool = Field(default=False, description="当前轮次是否已判分")
    round_answers: list[RoundAnswerItem] = Field(default_factory=list,
                                                 description="当前轮次玩家答题详情")
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


class KickUserData(BaseModel):
    user_id: int = Field(..., ge=1, description="被踢出的用户ID")


class KickUserMessage(BaseModel):
    event: Literal[15] = GameEventType.KICK_USER.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: KickUserData


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


class TagsUpdateData(BaseModel):
    """标签增量更新数据"""
    added_tags: list[TagResponse] = Field(default_factory=list, description="新增的标签")
    updated_tags: list[TagResponse] = Field(default_factory=list, description="更新的标签")
    deleted_tag_ids: list[int] = Field(default_factory=list, description="删除的标签ID")


class TagsUpdateMessage(BaseModel):
    """标签增量更新消息"""
    event: Literal[60] = GameEventType.TAGS_UPDATE.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: TagsUpdateData

    model_config = ConfigDict(from_attributes=True)


class TagGroupsUpdateData(BaseModel):
    """标签组增量更新数据"""
    added_tag_groups: list[TagGroupResponse] = Field(default_factory=list,
                                                     description="新增的标签组")
    updated_tag_groups: list[TagGroupResponse] = Field(default_factory=list,
                                                       description="更新的标签组")
    deleted_tag_group_ids: list[int] = Field(default_factory=list,
                                             description="删除的标签组ID")


class TagGroupsUpdateMessage(BaseModel):
    """标签组增量更新消息"""
    event: Literal[61] = GameEventType.TAG_GROUPS_UPDATE.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: TagGroupsUpdateData

    model_config = ConfigDict(from_attributes=True)


class TagGroupData(BaseModel):
    """房间维度标签组同步数据"""
    room_id: str
    tag_groups: list[RoomStateTagGroupItem] = Field(default_factory=list)


class TagGroupMessage(BaseModel):
    """房间维度标签组同步消息（避免发送全量 ROOM_STATE）"""
    event: Literal[62] = GameEventType.TAG_GROUP.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: TagGroupData

    model_config = ConfigDict(from_attributes=True)
