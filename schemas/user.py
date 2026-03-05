from __future__ import annotations
from pydantic import BaseModel, Field, ConfigDict


class BaseUser(BaseModel):
    username: str = Field(..., description="用户名")
    is_owner: bool = Field(..., description="是否为房主")

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseUser):
    pass


class UserLogin(BaseUser):
    id: int = Field(..., description="用户 ID")
    token: str = Field(..., description="用户令牌")
