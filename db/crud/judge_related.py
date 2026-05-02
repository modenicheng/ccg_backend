"""Judge-related CRUD Functions"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from utils import get_logger

from .. import models
from .room_song_related import fetch_room_object

logger = get_logger(__name__)


async def get_room_song_queue(
    session: AsyncSession,
    room_id: str,
) -> list[int]:
    """获取房间的有序歌曲队列（按song_order排序的歌曲ID列表）"""
    stmt = (select(models.RoomSong.song_id).where(
        models.RoomSong.room_id == room_id,
        models.RoomSong.song_order.is_not(None)).order_by(models.RoomSong.song_order))

    result = await session.execute(stmt)
    song_ids = result.scalars().all()
    return list(song_ids)


async def get_current_song_info(
    session: AsyncSession,
    room_id: str,
) -> tuple[int | None, int | None]:
    """获取房间的当前歌曲信息

    Returns:
        (song_id, song_index) 元组，如果未设置则返回 (None, None)
    """
    # 获取房间的当前歌曲索引
    stmt = select(models.Room.current_song_index).where(models.Room.id == room_id)
    result = await session.execute(stmt)
    song_index = result.scalar_one_or_none()

    if song_index is None:
        return None, None

    # 获取歌曲队列
    song_queue = await get_room_song_queue(session, room_id)

    if not song_queue or song_index < 0 or song_index >= len(song_queue):
        return None, None

    song_id = song_queue[song_index]
    return song_id, song_index


async def get_player_answers_for_judging(
    session: AsyncSession,
    room_id: str,
    song_id: int,
    round_index: int,
) -> dict[int, dict[str, Any]]:
    """获取指定房间、歌曲、轮次的所有玩家答案

    Returns:
        字典，键为玩家ID，值为答案数据
    """

    stmt = (select(models.PlayerAnswer).where(
        models.PlayerAnswer.room_id == room_id,
        models.PlayerAnswer.song_id == song_id,
        models.PlayerAnswer.round_index == round_index,
    ).options(selectinload(models.PlayerAnswer.user)))

    result = await session.execute(stmt)
    answers = result.scalars().all()

    player_answers = {}
    for answer in answers:
        player_answers[answer.user_id] = {
            "user_id": answer.user_id,
            "selected_tag_ids": answer.selected_tag_ids or [],
            "description_text": answer.description_text,
            "answer_order": answer.answer_order,
            "created_at": answer.created_at,
        }

    return player_answers


async def get_tag_group_map(
    session: AsyncSession,
    room_id: str,
) -> dict[int, list[int]]:
    """获取房间的标签组映射

    Returns:
        字典，键为标签组ID，值为该标签组包含的标签ID列表
    """
    # 使用fetch_room_object获取完整的房间对象
    room = await fetch_room_object(session, room_id)
    if not room:
        return {}

    tag_group_map = {}
    for tag_group in room.tag_groups:
        tag_group_map[tag_group.id] = [tag.id for tag in tag_group.tags]

    return tag_group_map


async def update_player_answer_order(
    session: AsyncSession,
    room_id: str,
    song_id: int,
    round_index: int,
    answer_queue: list[int],
) -> int:
    """更新玩家答案的抢答顺序

    Args:
        session: 数据库会话
        room_id: 房间ID
        song_id: 歌曲ID
        round_index: 轮次索引
        answer_queue: 已排序的玩家ID列表（字符串格式）

    Returns:
        更新的记录数
    """
    # Batch fetch all answers for this round
    stmt = select(models.PlayerAnswer).where(
        models.PlayerAnswer.room_id == room_id,
        models.PlayerAnswer.song_id == song_id,
        models.PlayerAnswer.round_index == round_index,
    )
    result = await session.execute(stmt)
    all_answers = list(result.scalars().all())

    # Build lookup: user_id -> latest answer (by created_at desc)
    # Since there could be multiple answers per user, pick the latest
    user_answers: dict[int, models.PlayerAnswer] = {}
    for answer in all_answers:
        existing = user_answers.get(answer.user_id)
        if existing is None or (answer.created_at or
                                datetime.min) > (existing.created_at or datetime.min):
            user_answers[answer.user_id] = answer

    updated_count = 0
    for order, user_id in enumerate(answer_queue, start=1):
        player_answer = user_answers.get(user_id)
        if player_answer:
            player_answer.answer_order = order
            updated_count += 1

    await session.flush()
    return updated_count


async def save_score_record(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    session: AsyncSession,
    room_id: str,
    user_id: int,
    round_index: int | None,
    score_delta: int,
    total_score: int | None = None,
) -> models.Score:
    """保存得分记录

    Args:
        session: 数据库会话
        room_id: 房间ID
        user_id: 用户ID
        round_index: 轮次索引
        score_delta: 本轮得分变化
        total_score: 累计总分（如果为None则自动计算）

    Returns:
        创建的Score记录
    """
    if total_score is None:
        # 计算该用户当前累计总分
        stmt = select(func.sum(models.Score.score_delta)).where(
            models.Score.room_id == room_id, models.Score.user_id == user_id)
        result = await session.execute(stmt)
        current_total = result.scalar() or 0
        total_score = current_total + score_delta

    score = models.Score(
        room_id=room_id,
        user_id=user_id,
        round_index=round_index,
        score_delta=score_delta,
        total_score=total_score,
    )

    session.add(score)
    await session.flush()
    return score


async def get_round_answers_for_room_state(
    session: AsyncSession,
    room_id: str,
    song_id: int,
    round_index: int,
) -> list[dict[str, Any]]:
    """获取 ROOM_STATE 需要的当前轮次玩家答题详情。"""
    tag_group_map = await get_tag_group_map(session, room_id)

    tag_id_to_group_id: dict[int, int] = {}
    for group_id, tag_ids in tag_group_map.items():
        for tag_id in tag_ids:
            tag_id_to_group_id[tag_id] = group_id

    stmt = (select(models.PlayerAnswer).where(
        models.PlayerAnswer.room_id == room_id,
        models.PlayerAnswer.song_id == song_id,
        models.PlayerAnswer.round_index == round_index,
    ).options(selectinload(models.PlayerAnswer.user)).order_by(
        models.PlayerAnswer.answer_order.is_(None),
        models.PlayerAnswer.answer_order.asc(),
        models.PlayerAnswer.created_at.asc(),
    ))
    result = await session.execute(stmt)
    answers = result.scalars().all()

    round_answers: list[dict[str, Any]] = []
    for index, answer in enumerate(answers, start=1):
        selected_tag_ids = answer.selected_tag_ids or []
        answers_by_group: dict[int, int] = {}
        for tag_id in selected_tag_ids:
            group_id = tag_id_to_group_id.get(tag_id)
            if group_id is not None and group_id not in answers_by_group:
                answers_by_group[group_id] = tag_id

        round_answers.append({
            "player_id":
                answer.user_id,
            "username":
                answer.user.username if answer.user else f"Player {answer.user_id}",
            "answers":
                answers_by_group,
            "description":
                answer.description_text,
            "order":
                answer.answer_order or index,
        })

    return round_answers
