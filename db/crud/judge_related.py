"""Judge-related CRUD Functions"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from utils import get_logger

from .. import models
from .room_song_related import fetch_room_object

l = get_logger(__name__)


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
    answer_queue: list[str],
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
    updated_count = 0

    for order, player_id_str in enumerate(answer_queue, start=1):
        try:
            user_id = int(player_id_str)
        except ValueError:
            continue

        # 查找该玩家的答案记录（最新的）
        stmt = (select(models.PlayerAnswer).where(
            models.PlayerAnswer.room_id == room_id,
            models.PlayerAnswer.user_id == user_id,
            models.PlayerAnswer.song_id == song_id,
            models.PlayerAnswer.round_index == round_index,
        ).order_by(models.PlayerAnswer.created_at.desc()).limit(1))

        result = await session.execute(stmt)
        player_answer = result.scalar_one_or_none()

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
