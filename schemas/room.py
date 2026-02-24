from pydantic import BaseModel, Field


class CreateRoomRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=32, description="房主用户名")


class CreateRoomResponse(BaseModel):
    roomId: str = Field(..., description="房间 ID")
    playerId: str = Field(..., description="创建者玩家 ID")
    token: str = Field(..., description="会话令牌")


class RoomInfoResponse(BaseModel):
    roomId: str
    hostPlayerId: str
    status: str
    title: str | None = None
    description: str | None = None
    players: list[str] = Field(default_factory=list)
    songQueue: list[str] = Field(default_factory=list)
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
