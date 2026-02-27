from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Optional

# 这个文件定义了所有的 Pydantic 模型，用于 Redis 数据的序列化和反序列化


class TaskResult(BaseModel):
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
