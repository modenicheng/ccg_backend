"""缓存层 schema 与序列化工具。

当前架构：
- Redis：播放状态（playback）与抢答队列（answer_queue）等热点状态
- SQL：房间基础状态与玩家在线状态

本文件中的模型用于两类场景：
1) Redis Hash / ZSet 的序列化与反序列化
2) 与 WebSocket schema 保持结构一致的兼容数据模型
"""

from __future__ import annotations

from typing import Any, Optional, Self

import orjson
from pydantic import BaseModel, ConfigDict, Field, field_validator

from utils.enumerations import RoomStatus
from schemas.ws_messages import room_schemas as RoomSchemas
from db.models import RoomStatusORM

# 本文件定义缓存层使用的 Pydantic 模型（含 Redis 序列化能力）


class RedisModel(BaseModel):
    """用于 Redis 数据结构的基础模型，提供序列化工具方法。"""

    def to_redis_hash(self) -> dict[str, str | int | float]:
        """
        将模型转换为适合 Redis Hash 存储的字典。
        规则：
        - 布尔值 -> 0/1 (int)
        - 整数/浮点数/字符串 -> 原样保留
        - 其他复杂类型（列表、字典、嵌套模型） -> JSON 字符串
        - 值为 None 的字段被跳过（可选）
        """
        result = {}
        for key, value in self.model_dump().items():
            if value is None:
                continue  # 或者 result[key] = ""，根据业务决定
            if isinstance(value, bool):
                result[key] = int(value)  # True -> 1, False -> 0
            elif isinstance(value, (int, float, str)):
                result[key] = value
            else:
                # 复杂类型：列表、字典、嵌套模型等统一转为 JSON 字符串
                result[key] = orjson.dumps(
                    value,  # type: ignore[attr-defined]
                    option=orjson.OPT_NON_STR_KEYS,  # type: ignore[attr-defined]
                    default=str,
                ).decode("utf-8")
        return result

    @classmethod
    def from_redis_hash(cls, data: dict[str, str]) -> Self:
        """
        从 Redis Hash 返回的字典重建模型。
        自动处理：
        - 简单字符串 -> Pydantic 会根据字段类型自动转换（如 "1" -> 1, "0" -> False）
        - JSON 字符串 -> 根据目标字段类型反序列化
        """
        parsed = {}
        for key, value_str in data.items():
            field_info = cls.model_fields.get(key)
            if not field_info:
                continue  # 忽略未知字段

            # 如果字段类型是 list, dict 或另一个 BaseModel，尝试解析 JSON
            if value_str and (value_str[0] in ("[", "{")):  # 简单启发式判断
                try:
                    parsed[key] = orjson.loads(value_str)
                except orjson.JSONDecodeError:
                    # 如果不是合法 JSON，保留原字符串
                    parsed[key] = value_str
            else:
                parsed[key] = value_str

        # model_validate 会进行类型转换和验证（包括字符串转 int/bool 等）
        return cls.model_validate(parsed)


class TaskResult(RedisModel):
    """Schema for representing the result of an asynchronous task."""

    task_id: str = Field(..., description="Unique identifier for the task")
    status: str = Field(
        ...,
        description=
        "Current status of the task (e.g., 'pending', 'completed', 'failed')",
    )
    result: Optional[Any] = Field(
        default=None, description="Result of the task if completed successfully")
    error: Optional[str] = Field(default=None,
                                 description="Error message if the task failed")


class RoomStateTagItem(RedisModel):
    """Redis model for room state tag item."""

    id: int
    name: str


class RoomStateTagGroupItem(RedisModel):
    """Redis model for room state tag group item."""

    id: int
    name: str
    description: str | None = None
    tags: list[RoomStateTagItem] = Field(default_factory=list)


class RoomStatePlayerItem(RedisModel):
    """Redis model for room state player item."""

    id: int = Field(description="玩家数据库ID")
    username: str = Field(description="玩家用户名")
    is_owner: bool = Field(default=False)
    online: bool = Field(default=False)

    model_config = ConfigDict(from_attributes=True)


class AnswerQueueItem(RedisModel, RoomSchemas.AnswerQueueItem):
    """Redis model for answer queue item."""

    model_config = ConfigDict(from_attributes=True)


class PlaybackState(RedisModel, RoomSchemas.PlaybackState):
    """Redis model for playback state."""

    model_config = ConfigDict(from_attributes=True)


class RoomBaseStateCache(RedisModel):
    """Redis model for room base state."""

    # 兼容模型：房间基础状态已迁移 SQL，此模型仍用于状态拼装与传输结构统一。
    room_id: str
    title: str | None = None
    status: RoomStatusORM = RoomStatusORM.WAITING
    song_start_range_percent: float = Field(default=0, ge=0, le=100)
    tag_groups: list[RoomStateTagGroupItem] = Field(default_factory=list)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> RoomStatusORM:  # pylint: disable=too-many-return-statements
        """Normalize status value to RoomStatusORM enum."""
        if isinstance(value, RoomStatusORM):
            return value

        if isinstance(value, RoomStatus):
            return RoomStatusORM(value.value)

        if isinstance(value, str):
            raw = value.strip()
            if raw.isdigit():
                value = int(raw)
            else:
                upper = raw.upper()
                if upper.startswith("ROOMSTATUSORM."):
                    upper = upper.split(".", 1)[1]
                if upper.startswith("ROOMSTATUS."):
                    upper = upper.split(".", 1)[1]
                if upper in RoomStatusORM.__members__:
                    return RoomStatusORM[upper]
                return RoomStatusORM.WAITING

        if isinstance(value, int):
            try:
                return RoomStatusORM(value)
            except ValueError:
                return RoomStatusORM.WAITING

        return RoomStatusORM.WAITING
