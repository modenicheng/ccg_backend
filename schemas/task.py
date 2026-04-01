"""Task queue request/response schemas.

Handles:
- Download and cache task requests
- Task execution responses
- Task status/error reporting
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class TaskStatusEnum(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"


class TaskDownloadRequest(BaseModel):
    """Request schema for downloading and caching a song."""

    songlist_id: int = Field(
        description="Database ID of the songlist containing these songs")
    song_ids: List[int] = Field(
        description="List of song database IDs to download and cache")
    max_concurrent: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional override for concurrent download limit")
    max_retries: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional override for max retry attempts per song")


class TaskDownloadResult(BaseModel):
    """Result for a single song download attempt."""

    song_id: int = Field(description="Song database ID")
    platform_song_id: Optional[str] = Field(description="Platform song ID")
    status: TaskStatusEnum = Field(description="Download status")
    cached_path: Optional[str] = Field(
        default=None, description="Path to cached file if success, null otherwise")
    error_message: Optional[str] = Field(default=None,
                                         description="Error message if failed/retry")
    attempts: int = Field(description="Number of attempts made")
    duration_seconds: Optional[float] = Field(
        default=None, description="Duration of successful download in seconds")


class TaskDownloadResponse(BaseModel):
    """Response schema for download and cache task completion."""

    task_id: Optional[str] = Field(default=None,
                                   description="Huey task ID for tracking in Redis")
    songlist_id: int = Field(description="Songlist ID being processed")
    total_songs: int = Field(description="Total songs requested for download")
    successful: int = Field(description="Number of successfully cached songs")
    failed: int = Field(description="Number of failed downloads")
    retried: int = Field(description="Number of songs that will be retried")
    results: List[TaskDownloadResult] = Field(
        description="Detailed result for each song")
    overall_status: TaskStatusEnum = Field(description="Overall task status")
    error_summary: Optional[str] = Field(default=None,
                                         description="Summary of any errors occurred")
    duration_seconds: Optional[float] = Field(
        default=None, description="Total task duration in seconds")
