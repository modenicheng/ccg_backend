from __future__ import annotations
from typing import TYPE_CHECKING
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from cache.room_cache import load_room_state, save_room_state, update_room_playback_state
from cache.schemas import RoomBaseStateCache
from db import models
from utils.enumerations import RoomStatus, RoundState
from utils import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class RoomStateMachine:
    """无状态房间状态机，负责验证和执行房间状态转换

    所有状态持久化在数据库和 Redis 缓存中，不持有实例状态。
    """

    # 允许的状态转移规则：当前状态 -> 可转移的目标状态列表
    TRANSITION_RULES: dict[RoomStatus, list[RoomStatus]] = {
        RoomStatus.WAITING: [RoomStatus.RUNNING],
        RoomStatus.RUNNING: [RoomStatus.ENDED],
        RoomStatus.ENDED: [RoomStatus.WAITING],  # 游戏结束后可重新开始
    }

    @classmethod
    def is_transition_allowed(cls, current: RoomStatus, target: RoomStatus) -> bool:
        """检查状态转移是否允许"""
        allowed_targets = cls.TRANSITION_RULES.get(current, [])
        return target in allowed_targets

    @classmethod
    async def transition(
        cls,
        session: AsyncSession,
        room_id: str,
        target: RoomStatus,
        **kwargs,
    ) -> bool:
        """执行房间状态转移

        Args:
            session: 数据库会话
            room_id: 房间ID
            target: 目标状态
            **kwargs: 额外参数，可用于扩展

        Returns:
            bool: 转移是否成功

        Raises:
            ValueError: 房间不存在或转移不允许
        """
        # 1. 从数据库加载房间
        room = await session.get(models.Room, room_id)
        if not room:
            logger.error(f"Room {room_id} not found in database")
            raise ValueError(f"Room {room_id} not found")

        current_status = RoomStatus(room.status)

        # 2. 验证转移是否允许
        if not cls.is_transition_allowed(current_status, target):
            logger.error(f"Invalid state transition for room {room_id}: "
                         f"{current_status.name} -> {target.name}")
            raise ValueError(
                f"Invalid state transition: {current_status.name} -> {target.name}")

        # 3. 更新数据库状态
        old_status = room.status
        room.status = models.RoomStatusORM(target.value)

        logger.info(f"Room {room_id} state transition: "
                    f"{RoomStatus(old_status).name} -> {target.name}")

        # 4. 更新 Redis 缓存
        try:
            cache_state = await load_room_state(room_id)
            if cache_state:
                # 更新缓存中的状态
                cache_state.status = target.value
                await save_room_state(room_id, cache_state)
                logger.debug(f"Updated Redis cache for room {room_id}")
            else:
                # 缓存不存在，创建新的缓存
                cache_state = RoomBaseStateCache(
                    room_id=room_id,
                    status=target.value,
                    title=room.title,
                    song_start_range_percent=0.0,
                    tag_groups=[],
                )
                await save_room_state(room_id, cache_state)
                logger.debug(f"Created new Redis cache for room {room_id}")
        except Exception as e:
            # Redis 更新失败不应影响整体状态转移，但需要记录日志
            logger.error(f"Failed to update Redis cache for room {room_id}: {e}")
            # 继续执行，因为数据库状态已更新

        return True

    @classmethod
    async def force_transition(
        cls,
        session: AsyncSession,
        room_id: str,
        target: RoomStatus,
        **kwargs,
    ) -> bool:
        """强制状态转移（绕过规则检查）

        用于特殊情况，如管理员操作或系统恢复。
        """
        room = await session.get(models.Room, room_id)
        if not room:
            logger.error(f"Room {room_id} not found in database")
            raise ValueError(f"Room {room_id} not found")

        old_status = room.status
        room.status = models.RoomStatusORM(target.value)

        logger.warning(f"Force transition for room {room_id}: "
                       f"{RoomStatus(old_status).name} -> {target.name}")

        # 更新 Redis 缓存
        try:
            cache_state = await load_room_state(room_id)
            if cache_state:
                cache_state.status = target.value
                await save_room_state(room_id, cache_state)
        except Exception as e:
            logger.error(f"Failed to update Redis cache for room {room_id}: {e}")

        return True


class RoundStateMachine:
    """无状态回合状态机，负责验证和执行回合状态转换

    所有状态持久化在数据库和 Redis 缓存中，不持有实例状态。
    """

    # 允许的状态转移规则：当前状态 -> 可转移的目标状态列表
    TRANSITION_RULES: dict[RoundState, list[RoundState]] = {
        RoundState.PENDING: [RoundState.PLAYING_AUDIO],
        RoundState.PLAYING_AUDIO: [RoundState.ANSWERING, RoundState.COMPLETED],
        RoundState.ANSWERING: [
            RoundState.PLAYING_AUDIO, RoundState.JUDGING, RoundState.COMPLETED
        ],
        RoundState.JUDGING: [RoundState.COMPLETED],
        RoundState.COMPLETED: [RoundState.PENDING],
    }

    @classmethod
    def is_transition_allowed(cls, current: RoundState, target: RoundState) -> bool:
        """检查状态转移是否允许"""
        allowed_targets = cls.TRANSITION_RULES.get(current, [])
        return target in allowed_targets

    @classmethod
    async def transition(
        cls,
        session: AsyncSession,
        room_id: str,
        target: RoundState,
        **kwargs,
    ) -> bool:
        """执行回合状态转移

        Args:
            session: 数据库会话
            room_id: 房间ID
            target: 目标状态
            **kwargs: 额外参数，可用于扩展

        Returns:
            bool: 转移是否成功

        Raises:
            ValueError: 房间不存在或转移不允许
        """
        # 1. 从数据库加载房间
        room = await session.get(models.Room, room_id)
        if not room:
            logger.error(f"Room {room_id} not found in database")
            raise ValueError(f"Room {room_id} not found")

        # 获取当前回合状态，默认为 PENDING
        current_round_state = RoundState(room.round_state or 0)

        # 2. 验证转移是否允许
        if not cls.is_transition_allowed(current_round_state, target):
            logger.error(f"Invalid round state transition for room {room_id}: "
                         f"{current_round_state.name} -> {target.name}")
            raise ValueError(
                f"Invalid round state transition: {current_round_state.name} -> {target.name}"
            )

        # 3. 更新数据库状态
        old_round_state = room.round_state
        room.round_state = target.value

        logger.info(f"Room {room_id} round state transition: "
                    f"{RoundState(old_round_state or 0).name} -> {target.name}")

        # 4. 更新 Redis 缓存
        try:
            # 更新房间播放状态中的回合状态
            await update_room_playback_state(
                room_id=room_id,
                round_state=target.name,
                progress_ms=0,
                offset_ts=0,
                audio_url=None,
                event_ts=0,
                event_name="round_state_update",
            )
            logger.debug(f"Updated Redis cache for room {room_id} round state")
        except Exception as e:
            # Redis 更新失败不应影响整体状态转移，但需要记录日志
            logger.error(
                f"Failed to update Redis cache for room {room_id} round state: {e}")
            # 继续执行，因为数据库状态已更新

        return True

    @classmethod
    async def force_transition(
        cls,
        session: AsyncSession,
        room_id: str,
        target: RoundState,
        **kwargs,
    ) -> bool:
        """强制回合状态转移（绕过规则检查）

        用于特殊情况，如管理员操作或系统恢复。
        """
        room = await session.get(models.Room, room_id)
        if not room:
            logger.error(f"Room {room_id} not found in database")
            raise ValueError(f"Room {room_id} not found")

        old_round_state = room.round_state
        room.round_state = target.value

        logger.warning(f"Force round state transition for room {room_id}: "
                       f"{RoundState(old_round_state or 0).name} -> {target.name}")

        # 更新 Redis 缓存
        try:
            await update_room_playback_state(
                room_id=room_id,
                round_state=target.name,
                progress_ms=0,
                offset_ts=0,
                audio_url=None,
                event_ts=0,
                event_name="force_round_state_update",
            )
        except Exception as e:
            logger.error(
                f"Failed to update Redis cache for room {room_id} round state: {e}")

        return True


# 示例用法：
# async def example_usage():
#     from db.session import session_scope
#     async with session_scope() as session:
#         try:
#             success = await RoomStateMachine.transition(
#                 session, "room_id", RoomStatus.RUNNING
#             )
#             print(f"Transition successful: {success}")
#         except ValueError as e:
#             print(f"Transition failed: {e}")

# async def example_round_usage():
#     from db.session import session_scope
#     async with session_scope() as session:
#         try:
#             success = await RoundStateMachine.transition(
#                 session, "room_id", RoundState.PLAYING_AUDIO
#             )
#             print(f"Round transition successful: {success}")
#         except ValueError as e:
#             print(f"Round transition failed: {e}")
