from __future__ import annotations

from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.models import Room, ScoreRecord, User
from db.crud import get_room_by_id, update_room_status
from .room_cache import (
    load_room_state, save_room_state, clear_answer_queue,
    set_room_playback_state, get_room_playback_state,
    get_room_players, save_room_players,
    set_room_start_position, get_room_start_position
)
from .schemas import RoomBaseStateCache, PlaybackState
from utils.enumerations import RoomStatus
from utils import get_logger

logger = get_logger(__name__)


class RoomStateManager:
    """房间状态管理类，统一管理房间状态（DB + Cache）"""

    @staticmethod
    async def get_room_state(room_id: str) -> Optional[RoomBaseStateCache]:
        """获取房间状态

        Args:
            room_id (str): 房间 ID

        Returns:
            Optional[RoomBaseStateCache]: 房间状态缓存对象，如果未找到则返回 None
        """
        return await load_room_state(room_id)

    @staticmethod
    async def save_room_state(room_id: str, state: RoomBaseStateCache) -> None:
        """保存房间状态

        Args:
            room_id (str): 房间 ID
            state (RoomBaseStateCache): 房间状态缓存对象
        """
        await save_room_state(room_id, state)

    @staticmethod
    async def set_start_position(room_id: str, position: float) -> bool:
        """设置房间起始位置

        Args:
            room_id (str): 房间 ID
            position (float): 起始位置百分比（0-80）

        Returns:
            bool: 是否设置成功
        """
        try:
            # 使用新添加的函数设置起始位置
            result = await set_room_start_position(room_id, position)
            if result:
                # 同时更新缓存对象
                state = await load_room_state(room_id)
                if state:
                    state.song_start_range_percent = position
                    await save_room_state(room_id, state)
            logger.debug(f"Set start position for room {room_id} to {position}%")
            return result
        except Exception as e:
            logger.error(f"Error setting start position for room {room_id}: {e}")
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
            logger.debug(f"Cleared answer queue for room {room_id}, result: {result}")
            return result is not None
        except Exception as e:
            logger.error(f"Error clearing answer queue for room {room_id}: {e}")
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
            # 更新数据库中的房间状态
            room = await get_room_by_id(session, room_id)
            if not room:
                logger.error(f"Room not found: {room_id}")
                return False

            # 验证状态转换
            if room.status != RoomStatus.WAITING:
                logger.error(f"Cannot start game, room {room_id} is in status {room.status}")
                return False

            # 更新数据库状态
            room.status = RoomStatus.RUNNING
            await session.commit()

            # 更新缓存状态
            state = await load_room_state(room_id)
            if state:
                state.status = RoomStatus.RUNNING.value
                await save_room_state(room_id, state)

            logger.info(f"Game started for room {room_id}")
            return True
        except Exception as e:
            logger.error(f"Error starting game for room {room_id}: {e}")
            await session.rollback()
            return False

    @staticmethod
    async def end_game(room_id: str, session: AsyncSession) -> Dict[str, Any]:
        """结束游戏

        Args:
            room_id (str): 房间 ID
            session (AsyncSession): 数据库会话

        Returns:
            Dict[str, Any]: 包含游戏结束信息的字典，包括最终得分
        """
        try:
            # 更新数据库中的房间状态
            room = await get_room_by_id(session, room_id)
            if not room:
                logger.error(f"Room not found: {room_id}")
                return {"success": False, "error": "Room not found"}

            # 验证状态转换
            if room.status != RoomStatus.RUNNING:
                logger.error(f"Cannot end game, room {room_id} is in status {room.status}")
                return {"success": False, "error": "Game is not running"}

            # 更新数据库状态
            room.status = RoomStatus.ENDED
            await session.commit()

            # 更新缓存状态
            state = await load_room_state(room_id)
            if state:
                state.status = RoomStatus.ENDED.value
                await save_room_state(room_id, state)

            # 获取最终得分
            score_records = await session.execute(
                select(ScoreRecord, User.username)
                .join(User, ScoreRecord.user_id == User.id)
                .where(ScoreRecord.room_id == room_id)
                .order_by(ScoreRecord.score.desc())
            )

            final_scores = []
            for record, username in score_records:
                final_scores.append({
                    "player_id": record.user_id,
                    "username": username,
                    "score": record.score
                })

            logger.info(f"Game ended for room {room_id}")
            return {
                "success": True,
                "final_scores": final_scores
            }
        except Exception as e:
            logger.error(f"Error ending game for room {room_id}: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

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
            logger.debug(f"Round ended for room {room_id}")
            return True
        except Exception as e:
            logger.error(f"Error ending round for room {room_id}: {e}")
            return False
