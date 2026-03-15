"""房间状态管理类，统一管理房间状态（DB）"""

# pylint: disable=broad-exception-caught

from __future__ import annotations

from typing import Any, Tuple

from sqlalchemy import Result, select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.enumerations import RoomStatus, RoundState
from utils import get_logger
from room_state.state_machine import RoomStateMachine, RoundStateMachine
from db.models import Score, User, Room
from db.session import session_scope
from db.crud.room_state_related import set_room_start_position as _db_set_start_position
from .room_cache import (
    clear_answer_queue,)

logger = get_logger(__name__)


class RoomStateManager:
    """房间状态管理类，统一管理房间状态（DB）"""

    @staticmethod
    async def set_start_position(room_id: str, position: float) -> bool:
        """设置房间起始位置，写入数据库。

        Args:
            room_id (str): 房间 ID
            position (float): 起始位置百分比（0-80）

        Returns:
            bool: 是否设置成功
        """
        try:
            async with session_scope() as db:
                result = await _db_set_start_position(db, room_id, position)
            logger.debug("Set start position for room %s to %.2f%%", room_id, position)
            return result
        except Exception as e:
            logger.error("Error setting start position for room %s: %s", room_id, e)
            return False

    @staticmethod
    async def clear_answer_queue(room_id: str) -> bool:
        """清空抢答队列

        Args:
            room_id (str): 房间 ID

        Returns:
            bool: 是否清空成功
        """
        try:
            result = await clear_answer_queue(room_id)
            logger.debug("Cleared answer queue for room %s, result: %s", room_id,
                         result)
            return result is not None
        except Exception as e:
            logger.error("Error clearing answer queue for room %s: %s", room_id, e)
            return False

    @staticmethod
    async def start_game(room_id: str, session: AsyncSession) -> bool:
        """开始游戏

        Args:
            room_id (str): 房间 ID
            session (AsyncSession): 数据库会话

        Returns:
            bool: 是否开始成功
        """
        try:
            # 使用 RoomStateMachine 处理状态转换
            await RoomStateMachine.transition(session, room_id, RoomStatus.RUNNING)
            # 游戏开始时重置回合状态为 PENDING
            await RoundStateMachine.force_transition(session, room_id,
                                                     RoundState.PENDING)
            logger.info("Game started for room %s", room_id)
            return True
        except ValueError as e:
            logger.error("Error starting game for room %s: %s", room_id, e)
            return False
        except Exception as e:
            logger.error("Error starting game for room %s: %s", room_id, e)
            await session.rollback()
            return False

    @staticmethod
    async def end_game(room_id: str, session: AsyncSession) -> dict[str, Any]:
        """结束游戏

        Args:
            room_id (str): 房间 ID
            session (AsyncSession): 数据库会话

        Returns:
            Dict[str, Any]: 包含游戏结束信息的字典，包括最终得分
        """
        try:
            # 使用 RoomStateMachine 处理状态转换
            await RoomStateMachine.transition(session, room_id, RoomStatus.ENDED)
            # 游戏结束时回合状态重置为 PENDING
            await RoundStateMachine.force_transition(session, room_id,
                                                     RoundState.PENDING)

            # 获取最终得分
            score_records: Result[Tuple[Score, str]] = await session.execute(
                select(Score, User.username).join(User, Score.user_id == User.id).where(
                    Score.room_id == room_id).order_by(Score.total_score.desc()))

            final_scores = []
            for record, username in score_records:
                final_scores.append({
                    "player_id": record.user_id,
                    "username": username,
                    "score": record.total_score or 0,
                })

            logger.info("Game ended for room %s", room_id)
            return {"success": True, "final_scores": final_scores, "error": None}
        except ValueError as e:
            logger.error("Error ending game for room %s: %s", room_id, e)
            return {"success": False, "final_scores": [], "error": str(e)}
        except Exception as e:
            logger.error("Error ending game for room %s: %s", room_id, e)
            await session.rollback()
            return {"success": False, "final_scores": [], "error": str(e)}

    @staticmethod
    async def end_round(room_id: str) -> bool:
        """结束回合

        Args:
            room_id (str): 房间 ID

        Returns:
            bool: 是否结束成功
        """
        try:
            # 清空抢答队列
            await clear_answer_queue(room_id)

            # 可以在这里添加其他回合结束的逻辑
            logger.debug("Round ended for room %s", room_id)
            return True
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error ending round for room %s: %s", room_id, e)
            return False


class RoundStateManager:
    """回合状态管理类，统一管理回合状态（DB + Cache）"""

    @staticmethod
    async def get_round_state(room_id: str, session: AsyncSession) -> RoundState | None:
        """获取回合状态"""
        room = await session.get(Room, room_id)
        if not room:
            return None
        return RoundState(room.round_state or RoundState.PENDING.value)

    @staticmethod
    async def transition_round_state(
        room_id: str,
        target: RoundState,
        session: AsyncSession,
        force: bool = False,
    ) -> bool:
        """切换回合状态"""
        try:
            if force:
                await RoundStateMachine.force_transition(session, room_id, target)
            else:
                await RoundStateMachine.transition(session, room_id, target)
            logger.info("Round state transitioned for room %s -> %s", room_id,
                        target.name)
            return True
        except ValueError as e:
            logger.error("Error transitioning round state for room %s: %s", room_id, e)
            return False
        except Exception as e:
            logger.error("Error transitioning round state for room %s: %s", room_id, e)
            await session.rollback()
            return False

    @staticmethod
    async def reset_round_state(room_id: str, session: AsyncSession) -> bool:
        """将回合状态重置为 PENDING"""
        return await RoundStateManager.transition_round_state(
            room_id=room_id,
            target=RoundState.PENDING,
            session=session,
            force=True,
        )
