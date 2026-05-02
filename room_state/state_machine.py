"""State machine implementations for room and round management."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from db import models
from utils.enumerations import RoomStatus, RoundState
from utils import get_logger

logger = get_logger(__name__)


class RoomStateMachine:
    """无状态房间状态机，负责验证和执行房间状态转换

    所有状态持久化在数据库中，不持有实例状态。
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
        **_kwargs,
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
            logger.error("Room %s not found in database", room_id)
            raise ValueError(f"Room {room_id} not found")

        current_status = RoomStatus(room.status)

        # 2. 验证转移是否允许
        if not cls.is_transition_allowed(current_status, target):
            logger.error(
                "Invalid state transition for room %s: %s -> %s",
                room_id,
                current_status.name,
                target.name,
            )
            raise ValueError(
                f"Invalid state transition: {current_status.name} -> {target.name}")

        # 3. 更新数据库状态
        old_status = room.status
        room.status = models.RoomStatusORM(target.value)

        logger.info(
            "Room %s state transition: %s -> %s",
            room_id,
            RoomStatus(old_status).name,
            target.name,
        )

        return True

    @classmethod
    async def force_transition(
        cls,
        session: AsyncSession,
        room_id: str,
        target: RoomStatus,
        **_kwargs,
    ) -> bool:
        """强制状态转移（绕过规则检查）

        用于特殊情况，如管理员操作或系统恢复。
        """
        room = await session.get(models.Room, room_id)
        if not room:
            logger.error("Room %s not found in database", room_id)
            raise ValueError(f"Room {room_id} not found")

        old_status = room.status
        room.status = models.RoomStatusORM(target.value)

        logger.warning(
            "Force transition for room %s: %s -> %s",
            room_id,
            RoomStatus(old_status).name,
            target.name,
        )

        return True


class RoundStateMachine:
    """无状态回合状态机，负责验证和执行回合状态转换

    所有状态持久化在数据库中，不持有实例状态。
    """

    # 允许的状态转移规则：当前状态 -> 可转移的目标状态列表
    TRANSITION_RULES: dict[RoundState, list[RoundState]] = {
        RoundState.PENDING: [RoundState.PLAYING_AUDIO],
        RoundState.PLAYING_AUDIO: [RoundState.ANSWERING, RoundState.COMPLETED],
        RoundState.ANSWERING: [
            RoundState.PLAYING_AUDIO,
            RoundState.JUDGING,
            RoundState.COMPLETED,
        ],
        RoundState.JUDGING: [RoundState.COMPLETED, RoundState.PLAYING_AUDIO],
        RoundState.COMPLETED: [RoundState.PENDING,
                               RoundState.PLAYING_AUDIO],  # 回合结束后可进入下一个回合或重新开始
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
        **_kwargs,
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
            logger.error("Room %s not found in database", room_id)
            raise ValueError(f"Room {room_id} not found")

        # 获取当前回合状态，默认为 PENDING
        current_round_state = RoundState(room.round_state or 0)

        # 允许幂等转换：目标状态与当前状态一致时直接返回成功
        # 这里不更新数据库和缓存，避免对播放状态产生不必要副作用
        if current_round_state == target:
            logger.info(
                "Round state transition is idempotent for room %s: remains %s",
                room_id,
                target.name,
            )
            return True

        # 2. 验证转移是否允许
        if not cls.is_transition_allowed(current_round_state, target):
            logger.error(
                "Invalid round state transition for room %s: %s -> %s",
                room_id,
                current_round_state.name,
                target.name,
            )
            raise ValueError(
                f"Invalid round state transition: {current_round_state.name} -> {target.name}"
            )

        # 3. 更新数据库状态
        old_round_state = room.round_state
        room.round_state = target.value

        logger.info(
            "Room %s round state transition: %s -> %s",
            room_id,
            RoundState(old_round_state or 0).name,
            target.name,
        )

        return True

    @classmethod
    async def force_transition(
        cls,
        session: AsyncSession,
        room_id: str,
        target: RoundState,
        **_kwargs,
    ) -> bool:
        """强制回合状态转移（绕过规则检查）

        用于特殊情况，如管理员操作或系统恢复。
        """
        room = await session.get(models.Room, room_id)
        if not room:
            logger.error("Room %s not found in database", room_id)
            raise ValueError(f"Room {room_id} not found")

        old_round_state = room.round_state
        room.round_state = target.value

        logger.warning(
            "Force round state transition for room %s: %s -> %s",
            room_id,
            RoundState(old_round_state or 0).name,
            target.name,
        )

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
