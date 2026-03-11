"""Tasks CRUD operations."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from utils import get_logger

from .. import models

l = get_logger(__name__)


async def create_task_record(
    session: AsyncSession,
    task_id: str,
    task_name: str,
    status: str,
    result_json: Optional[dict[str, Any]] = None,
) -> models.Tasks:
    """创建任务记录。并发场景下使用 UPSERT 防止 task_id 唯一键冲突。"""
    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""

    if dialect_name == "postgresql":
        stmt = (pg_insert(models.Tasks).values(
            task_id=task_id,
            task_name=task_name,
            status=status,
            result_json=result_json,
        ).on_conflict_do_update(
            index_elements=[models.Tasks.task_id],
            set_={
                "task_name": task_name,
                "status": status,
                "result_json": result_json,
            },
        ))
        await session.execute(stmt)
        task = await get_task_record_by_task_id(session=session, task_id=task_id)
        assert task is not None
        return task

    query = select(models.Tasks).where(models.Tasks.task_id == task_id)
    res = await session.execute(query)
    existing = res.scalars().first()
    if existing:
        existing.task_name = task_name
        existing.status = status
        existing.result_json = result_json
        await session.flush()
        return existing

    task = models.Tasks(task_id=task_id,
                        task_name=task_name,
                        status=status,
                        result_json=result_json)
    session.add(task)
    try:
        await session.flush()
        return task
    except IntegrityError:
        await session.rollback()
        res = await session.execute(query)
        existing = res.scalars().first()
        if not existing:
            raise
        existing.task_name = task_name
        existing.status = status
        existing.result_json = result_json
        await session.flush()
        return existing


async def get_task_record_by_task_id(session: AsyncSession,
                                     task_id: str) -> Optional[models.Tasks]:
    """按 Huey task_id 查询任务记录。"""
    query = select(models.Tasks).where(models.Tasks.task_id == task_id)
    res = await session.execute(query)
    return res.scalars().first()


async def update_task_record(
    session: AsyncSession,
    task_id: str,
    status: Optional[str] = None,
    result_json: Optional[dict[str, Any]] = None,
) -> Optional[models.Tasks]:
    """更新任务状态与结果。"""
    task = await get_task_record_by_task_id(session=session, task_id=task_id)
    if not task:
        return None

    if status is not None:
        task.status = status
    if result_json is not None:
        task.result_json = result_json

    await session.flush()
    return task
