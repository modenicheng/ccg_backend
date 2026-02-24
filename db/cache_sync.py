"""
缓存-数据库同步管理器
负责 Redis 缓存与 SQLite 数据库之间的数据同步
"""

import json
import logging
from datetime import datetime
from typing import Any, Optional, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cache.connection import get_redis
from cache.utils import RedisKeys, room_manager
from db.models import Room, User, PlayerAnswer, Song, Score
from db.session import session_scope
from utils import get_logger

logger = get_logger(__name__)


class CacheSyncManager:
    """缓存同步管理器"""

    @staticmethod
    async def sync_room_to_db(room_id: str) -> bool:
        """
        将 Redis 房间状态同步到数据库（房间关闭时触发）
        
        Args:
            room_id: 房间 ID
            
        Returns:
            bool: 是否同步成功
        """
        try:
            redis = await get_redis()
            if not redis:
                logger.error(f"Redis connection failed for room {room_id}")
                return False

            # 获取 Redis 中的房间数据
            room_key = RedisKeys.room(room_id)
            room_data = await redis.hgetall(room_key)
            
            if not room_data:
                logger.warning(f"Room {room_id} not found in Redis")
                return False

            # 读取轮次数据（如果存储在 Redis 中）
            rounds_key = f"room:{room_id}:rounds"
            rounds_json = await redis.get(rounds_key)
            rounds_data = json.loads(rounds_json) if rounds_json else None

            # 获取最终积分
            scores_key = f"room:{room_id}:final_scores"
            scores_json = await redis.get(scores_key)
            final_scores = json.loads(scores_json) if scores_json else None

            # 更新数据库
            async with session_scope() as session:
                room = await session.get(Room, room_id)
                if not room:
                    logger.warning(f"Room {room_id} not found in database")
                    return False

                # 同步房间状态字段
                room.status = room_data.get("status", "waiting")
                room.title = room_data.get("title", room.title)
                room.description = room_data.get("description", room.description)
                room.tag_groups_json = json.loads(
                    room_data.get("tag_groups", "{}") or "{}"
                ) if isinstance(room_data.get("tag_groups"), str) else room_data.get("tag_groups")
                
                # 记录游戏进度数据
                if rounds_data:
                    room.rounds_data = rounds_data
                
                # 记录最终积分
                if final_scores:
                    room.final_scores_json = final_scores
                
                # 标记房间结束时间
                if room.status == "ended" and not room.ended_at:
                    room.ended_at = datetime.now()

                await session.commit()
                logger.info(f"Room {room_id} synced to database successfully")
                return True

        except Exception as e:
            logger.error(f"Error syncing room {room_id} to database: {e}")
            return False

    @staticmethod
    async def restore_room_from_db(room_id: str) -> bool:
        """
        从数据库恢复房间到 Redis（缓存失效恢复）
        
        Args:
            room_id: 房间 ID
            
        Returns:
            bool: 是否恢复成功
        """
        try:
            async with session_scope() as session:
                # 从数据库读取房间
                room = await session.get(Room, room_id)
                if not room:
                    logger.warning(f"Room {room_id} not found in database")
                    return False

                # 如果房间已结束，不恢复到 Redis
                if room.status == "ended":
                    logger.info(f"Room {room_id} is ended, skip restoration")
                    return False

                # 重建 Redis 房间状态
                host_player_ids = [u.player_id for u in room.users if u.is_owner and u.player_id]
                host_player_id = host_player_ids[0] if host_player_ids else ""

                created = await room_manager.create_room(room_id, host_player_id)
                if not created:
                    logger.error(f"Failed to create room {room_id} in Redis")
                    return False

                # 恢复房间属性
                redis = await get_redis()
                if redis:
                    room_key = RedisKeys.room(room_id)
                    updates = {
                        "status": room.status or "waiting",
                        "title": room.title or "",
                        "description": room.description or "",
                        "tag_groups": json.dumps(room.tag_groups_json or {}, ensure_ascii=False),
                    }
                    await redis.hset(room_key, mapping=updates)
                    
                    # 恢复轮次数据
                    if room.rounds_data:
                        rounds_key = f"room:{room_id}:rounds"
                        await redis.set(rounds_key, json.dumps(room.rounds_data, ensure_ascii=False))
                    
                    # 恢复最终积分
                    if room.final_scores_json:
                        scores_key = f"room:{room_id}:final_scores"
                        await redis.set(scores_key, json.dumps(room.final_scores_json, ensure_ascii=False))

                # 恢复玩家列表
                for user in room.users:
                    if user.player_id:
                        await room_manager.add_player(room_id, user.player_id)

                logger.info(f"Room {room_id} restored from database successfully")
                return True

        except Exception as e:
            logger.error(f"Error restoring room {room_id} from database: {e}")
            return False

    @staticmethod
    async def cleanup_expired_rooms() -> int:
        """
        清理即将过期的房间数据（定期任务）
        
        Returns:
            int: 清理的房间数量
        """
        try:
            redis = await get_redis()
            if not redis:
                logger.error("Redis connection failed for cleanup")
                return 0

            # 扫描所有房间键
            cursor = 0
            cleaned_count = 0
            threshold = 600  # TTL < 10 分钟时触发同步

            while True:
                cursor, keys = await redis.scan(cursor, match="room:*", count=100)
                
                for key in keys:
                    # 跳过非房间状态的键
                    if ":" in key.split("room:")[1] and key.split("room:")[1].count(":") > 0:
                        continue
                    
                    ttl = await redis.ttl(key)
                    
                    # 如果 TTL 即将过期或已过期，进行同步
                    if ttl > 0 and ttl < threshold:
                        room_id = key.replace("room:", "")
                        if await CacheSyncManager.sync_room_to_db(room_id):
                            cleaned_count += 1
                        # 清理该房间的所有 Redis 键
                        await CacheSyncManager._cleanup_room_redis_keys(room_id)
                
                if cursor == 0:
                    break

            logger.info(f"Cleanup completed: {cleaned_count} rooms synced")
            return cleaned_count

        except Exception as e:
            logger.error(f"Error during cleanup_expired_rooms: {e}")
            return 0

    @staticmethod
    async def _cleanup_room_redis_keys(room_id: str) -> bool:
        """
        清除房间在 Redis 中的所有键
        
        Args:
            room_id: 房间 ID
            
        Returns:
            bool: 是否清理成功
        """
        try:
            redis = await get_redis()
            if not redis:
                return False

            # 构造需要清理的所有键
            keys_to_delete = [
                RedisKeys.room(room_id),
                RedisKeys.room_players(room_id),
                RedisKeys.room_ready(room_id),
                RedisKeys.room_song_queue(room_id),
                f"room:{room_id}:rounds",
                f"room:{room_id}:final_scores",
                f"room:{room_id}:attempts_queue",
                f"room:{room_id}:current_answerer",
            ]

            for key in keys_to_delete:
                await redis.delete(key)

            logger.debug(f"Cleaned up {len(keys_to_delete)} Redis keys for room {room_id}")
            return True

        except Exception as e:
            logger.error(f"Error cleaning up Redis keys for room {room_id}: {e}")
            return False

    @staticmethod
    async def sync_all_active_rooms() -> int:
        """
        同步所有活跃房间到数据库（应用关闭时调用）
        
        Returns:
            int: 同步的房间数量
        """
        try:
            redis = await get_redis()
            if not redis:
                logger.error("Redis connection failed for sync_all_active_rooms")
                return 0

            synced_count = 0
            cursor = 0

            while True:
                cursor, keys = await redis.scan(cursor, match="room:*", count=100)
                
                for key in keys:
                    # 只处理房间状态键
                    if ":" not in key.replace("room:", "", 1):
                        room_id = key.replace("room:", "")
                        if await CacheSyncManager.sync_room_to_db(room_id):
                            synced_count += 1

                if cursor == 0:
                    break

            logger.info(f"Synced all active rooms: {synced_count} rooms")
            return synced_count

        except Exception as e:
            logger.error(f"Error during sync_all_active_rooms: {e}")
            return 0
