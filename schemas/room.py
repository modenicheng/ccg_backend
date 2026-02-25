from pydantic import BaseModel, ConfigDict, Field

class RoomSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class CreateRoomRequest(BaseModel):
    title: str = Field(..., max_length=100, description="房间标题")
    host_name: str = Field(..., description="房主名称")

class CreateRoomResponse(BaseModel):
    room_id: str = Field(..., description="房间 ID")
    host_name: str = Field(..., description="房主名称")
    host_token: str = Field(..., description="房主令牌，用于后续管理房间")

class RoomInfoResponse(BaseModel):
    roomId: str
    hostPlayerId: str
    status: str
    title: str | None = None
    description: str | None = None
    players: list[str] = Field(default_factory=list)
    tagGroups: dict = Field(default_factory=dict)
    playProgress: int = 0


class PatchRoomRequest(BaseModel):
    songQueue: list[str] | None = Field(
        default=None,
        description="更新房间歌曲队列（song id 列表）",
    )
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    tagGroups: dict | None = Field(default=None, description="房间标签分组配置")
