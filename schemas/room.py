from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from time import time
from .song import SongItem
from db.models import Base, RoomStatusORM
from schemas.tag import TagGroupResponse
from utils.enumerations import GameEventType
from .user import BaseUser, UserLogin

from deprecated import deprecated


class RoomSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CreateRoomRequest(BaseModel):
    title: str = Field(..., max_length=100, description="房间标题")
    host_name: str = Field(..., description="房主名称")

    model_config = ConfigDict(from_attributes=True)


class CreateRoomResponse(BaseModel):
    room_id: str = Field(..., description="房间 ID")
    host: UserLogin = Field(..., description="房主信息")
    model_config = ConfigDict(from_attributes=True)


class RoomInfoResponse(BaseModel):
    room_id: str
    host_player_id: str
    status: int
    title: str | None = None
    players: list[BaseUser] = Field(default_factory=list)
    tag_groups: list[TagGroupResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class PatchRoomRequest(BaseModel):
    song_queue: list[str] | None = Field(
        default=None,
        description="更新房间歌曲队列（song id 列表）",
    )
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    tag_group_ids: list[int] | None = Field(default=None,
                                            description="房间关联的标签组 ID 列表")
    tag_groups: list[TagGroupResponse] | None = Field(default=None,
                                                      description="房间标签分组配置")
    model_config = ConfigDict(from_attributes=True)


class JoinRoomRequest(BaseModel):
    username: str = Field(..., description="用户名")

    model_config = ConfigDict(from_attributes=True)


class JoinRoomResponse(BaseModel):
    room_id: str = Field(..., description="房间 ID")
    user: UserLogin = Field(..., description="用户信息")

    model_config = ConfigDict(from_attributes=True)


class RoomStateTagItem(BaseModel):
    id: int
    name: str


class RoomStateTagGroupItem(BaseModel):
    id: int
    name: str
    description: str | None = None
    tags: list[RoomStateTagItem] = Field(default_factory=list)


class RoomStatePlayerItem(BaseModel):
    id: int
    username: str
    is_owner: bool
    online: bool = True


class AnswerQueueItem(BaseModel):
    player_id: int
    order: int | None = None
    is_answering: bool = False


class PlaybackState(BaseModel):
    progress_ms: int = Field(default=0, ge=0)
    play_state: Literal["playing", "paused"] = Field(default="paused")
    song_id: int | None = None
    current_order: int = Field(default=0, ge=0)

# 这是 AI 生成的屎，由一百万个重复数据
# 别用
@deprecated(
    reason=
    "This schema is deprecated and will be removed in future versions. Use ClientRoomState instead.",
)
class RoomStateInitData(BaseModel):
    room_id: str
    title: str | None = None
    status: int
    host: str | None = None
    owner: str | None = None
    host_player_id: str
    players: list[RoomStatePlayerItem] = Field(default_factory=list)
    tag_groups: list[RoomStateTagGroupItem] = Field(default_factory=list)
    tags: list[RoomStateTagItem] = Field(default_factory=list)


#这是手写的，用这个
class ClientRoomState(BaseModel):
    room_id: str
    title: str | None = None
    status: Literal[0, 1, 2] = RoomStatusORM.WAITING.value
    song_start_range_percent: float = Field(default=0, ge=0, le=100)
    players: list[RoomStatePlayerItem] = Field(default_factory=list)
    tag_groups: list[RoomStateTagGroupItem] = Field(default_factory=list)
    answer_queue: list[AnswerQueueItem] = Field(default_factory=list)
    playback_status: PlaybackState = Field(..., description="当前播放状态")
    audio_url: str | None = None


class FullRoomState(ClientRoomState):
    song_queue: list[int] = Field(default_factory=list)


# deprecated
@deprecated(
    reason=
    "This schema is deprecated and will be removed in future versions. Use ClientRoomState instead.",
)
class RoomStateInitMessage(BaseModel):
    event: Literal[12] = GameEventType.ROOM_STATE.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: RoomStateInitData


class RoomStateMessage(BaseModel):
    event: Literal[12] = GameEventType.ROOM_STATE.value
    ts: int = Field(default_factory=lambda: int(time() * 1000))
    data: ClientRoomState

    model_config = ConfigDict(from_attributes=True)
