import orjson
from datetime import datetime, timezone
from typing import Any, Optional, Self
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from time import time
from utils.enumerations import RoomStatus, GameEventType
from schemas.ws_messages import room_schemas as RoomSchemas
from pydantic import BaseModel, Field

# 这个文件定义了所有的 Pydantic 模型，用于 Redis 数据的序列化和反序列化


class RedisModel(BaseModel):
    """Base model for Redis data structures, with utility methods for serialization."""

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
                result[key] = orjson.dumps(value,
                                           option=orjson.OPT_NON_STR_KEYS,
                                           default=str).decode("utf-8")
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
            if value_str and (value_str[0] in ('[', '{')):  # 简单启发式判断
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
        "Current status of the task (e.g., 'pending', 'completed', 'failed')")
    result: Optional[Any] = Field(
        default=None,
        description="Result of the task if completed successfully")
    error: Optional[str] = Field(
        default=None, description="Error message if the task failed")


class RoomStateTagItem(RedisModel):
    id: int
    name: str


class RoomStateTagGroupItem(RedisModel):
    id: int
    name: str
    description: str | None = None
    tags: list[RoomStateTagItem] = Field(default_factory=list)


class RoomStatePlayerItem(RedisModel):
    id: int = Field(description="玩家数据库ID")
    username: str = Field(description="玩家用户名")
    is_owner: bool = Field(default=False)
    online: bool = Field(default=False)

    model_config = ConfigDict(from_attributes=True)


# 下面的模型直接继承自 RoomSchemas 中的 Pydantic 模型，并添加 RedisModel 的功能
# 保持 cache 和 ws_messages 中的模型结构一致，方便数据转换和维护
class AnswerQueueItem(RedisModel, RoomSchemas.AnswerQueueItem):
    model_config = ConfigDict(from_attributes=True)
    pass


class PlaybackState(RedisModel, RoomSchemas.PlaybackState):
    model_config = ConfigDict(from_attributes=True)
    pass


class RoomBaseStateCache(RedisModel):
    room_id: str
    title: str | None = None
    status: Literal[0, 1, 2] = RoomStatus.WAITING.value
    song_start_range_percent: float = Field(default=0, ge=0, le=100)
    tag_groups: list[RoomStateTagGroupItem] = Field(default_factory=list)
