from pydantic import BaseModel, ConfigDict, Field

from schemas.tag import TagGroupResponse
from .user import BaseUser, UserLogin


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
