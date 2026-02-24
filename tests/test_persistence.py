"""
持久化功能集成测试
测试 Redis 缓存与 SQLite 数据库的同步功能
"""

import asyncio
import json
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from cache.connection import redis_client
from cache.utils import RedisKeys, room_manager
from db import crud
from db.cache_sync import CacheSyncManager
from db.models import Room, User
from db.session import session_scope


class TestPersistence:
    """持久化功能测试"""

    @pytest.mark.asyncio
    async def test_create_room_persists_to_db(self):
        """测试：创建房间时同时持久化到数据库"""
        room_id = f"TEST{uuid4().hex[:8]}"
        player_id = uuid4().hex[:12]

        async with session_scope() as session:
            # 创建房间到数据库
            room = await crud.create_room_in_db(session, room_id, "Test Room", "Test Description")
            assert room is not None
            assert room.id == room_id
            assert room.title == "Test Room"
            assert room.status == "waiting"

            # 验证数据库中有该房间
            db_room = await session.get(Room, room_id)
            assert db_room is not None

    @pytest.mark.asyncio
    async def test_add_user_to_room(self):
        """测试：添加用户到房间"""
        room_id = f"TEST{uuid4().hex[:8]}"
        player_id = uuid4().hex[:12]
        username = f"user_{uuid4().hex[:8]}"

        async with session_scope() as session:
            # 创建房间
            await crud.create_room_in_db(session, room_id)

            # 添加用户到房间
            user = await crud.add_user_to_room(session, room_id, player_id, username, is_owner=True)
            assert user is not None
            assert user.player_id == player_id
            assert user.username == username
            assert user.room_id == room_id
            assert user.is_owner

            # 验证用户记录
            db_user = await session.get(User, user.id)
            assert db_user is not None
            assert db_user.joined_at is not None

    @pytest.mark.asyncio
    async def test_sync_room_to_db(self):
        """测试：从 Redis 同步房间到数据库"""
        room_id = f"TEST{uuid4().hex[:8]}"
        player_id = uuid4().hex[:12]

        # 设置 Redis 中的房间数据
        redis = await redis_client.get_client()
        if redis:
            room_key = RedisKeys.room(room_id)
            await redis.hset(room_key, mapping={
                "host_player_id": player_id,
                "status": "playing",
                "title": "Test Room",
                "description": "Test Description",
                "tag_groups": json.dumps({"group1": ["tag1", "tag2"]}),
            })

            # 创建数据库房间
            async with session_scope() as session:
                await crud.create_room_in_db(session, room_id, "Test Room", "Test Description")

            # 同步从 Redis 到数据库
            synced = await CacheSyncManager.sync_room_to_db(room_id)
            assert synced

            # 验证数据库中的房间已更新
            async with session_scope() as session:
                db_room = await session.get(Room, room_id)
                assert db_room.status == "playing"

    @pytest.mark.asyncio
    async def test_restore_room_from_db(self):
        """测试：从数据库恢复房间到 Redis"""
        room_id = f"TEST{uuid4().hex[:8]}"
        player_id = uuid4().hex[:12]
        username = f"user_{uuid4().hex[:8]}"

        async with session_scope() as session:
            # 在数据库中创建房间和用户
            room = await crud.create_room_in_db(session, room_id, "Test Room", "Test Description")
            user = await crud.add_user_to_room(session, room_id, player_id, username, is_owner=True)
            assert room and user

        # 从数据库恢复到 Redis
        restored = await CacheSyncManager.restore_room_from_db(room_id)
        assert restored

        # 验证 Redis 中有房间数据
        redis = await redis_client.get_client()
        if redis:
            room_key = RedisKeys.room(room_id)
            room_data = await redis.hgetall(room_key)
            assert room_data is not None
            assert room_data.get("status") == "waiting"
            assert room_data.get("title") == "Test Room"

    @pytest.mark.asyncio
    async def test_update_room_status(self):
        """测试：更新房间状态"""
        room_id = f"TEST{uuid4().hex[:8]}"

        async with session_scope() as session:
            # 创建房间
            await crud.create_room_in_db(session, room_id)

            # 更新状态为 playing
            updated = await crud.update_room_status(session, room_id, "playing")
            assert updated

            # 验证数据库中的状态已更新
            db_room = await session.get(Room, room_id)
            assert db_room.status == "playing"
            assert db_room.started_at is not None

    @pytest.mark.asyncio
    async def test_update_room_scores(self):
        """测试：更新房间最终积分"""
        room_id = f"TEST{uuid4().hex[:8]}"
        final_scores = {
            "player1": 100,
            "player2": 80,
            "player3": 60,
        }

        async with session_scope() as session:
            # 创建房间
            await crud.create_room_in_db(session, room_id)

            # 更新积分
            updated = await crud.update_room_scores(session, room_id, final_scores)
            assert updated

            # 验证数据库中的积分已更新
            db_room = await session.get(Room, room_id)
            assert db_room.final_scores_json == final_scores

    @pytest.mark.asyncio
    async def test_mark_user_left_room(self):
        """测试：标记用户离开房间"""
        room_id = f"TEST{uuid4().hex[:8]}"
        player_id = uuid4().hex[:12]
        username = f"user_{uuid4().hex[:8]}"

        async with session_scope() as session:
            # 创建房间和用户
            await crud.create_room_in_db(session, room_id)
            user = await crud.add_user_to_room(session, room_id, player_id, username)
            assert user

            user_id = user.id

        # 标记用户离开
        async with session_scope() as session:
            marked = await crud.mark_user_left_room(session, user_id)
            assert marked

            # 验证用户已标记离开
            db_user = await session.get(User, user_id)
            assert db_user.left_at is not None
            assert db_user.room_id is None

    @pytest.mark.asyncio
    async def test_cleanup_expired_rooms(self):
        """测试：清理即将过期的房间"""
        # 这个测试主要验证函数不会报错
        # 需要在实际环境中运行才能完整测试
        cleaned = await CacheSyncManager.cleanup_expired_rooms()
        assert isinstance(cleaned, int)
        assert cleaned >= 0


class TestPersistenceFlow:
    """完整的持久化流程测试"""

    @pytest.mark.asyncio
    async def test_full_room_lifecycle(self):
        """测试：完整的房间生命周期 - 创建、更新、关闭、持久化"""
        room_id = f"TEST{uuid4().hex[:8]}"
        player_id = uuid4().hex[:12]

        # 第一步：创建房间（数据库 + Redis）
        async with session_scope() as session:
            db_room = await crud.create_room_in_db(session, room_id, "Full Test Room")
            assert db_room is not None

            db_user = await crud.add_user_to_room(session, room_id, player_id, f"owner_{player_id}", is_owner=True)
            assert db_user is not None

        # 第二步：在 Redis 中创建房间
        created = await room_manager.create_room(room_id, player_id)
        assert created

        # 第三步：模拟游戏进行 - 更新状态
        async with session_scope() as session:
            await crud.update_room_status(session, room_id, "playing")

        # 第四步：更新 Redis 中的游戏状态
        redis = await redis_client.get_client()
        if redis:
            room_key = RedisKeys.room(room_id)
            await redis.hset(room_key, "status", "playing")

        # 第五步：游戏结束 - 同步到数据库
        final_scores = {player_id: 100}

        redis = await redis_client.get_client()
        if redis:
            scores_key = f"room:{room_id}:final_scores"
            await redis.set(scores_key, json.dumps(final_scores))

        async with session_scope() as session:
            await crud.update_room_status(session, room_id, "ended")
            await crud.update_room_scores(session, room_id, final_scores)

        # 第六步：同步 Redis 到数据库
        synced = await CacheSyncManager.sync_room_to_db(room_id)
        assert synced

        # 第七步：验证所有数据都被持久化
        async with session_scope() as session:
            db_room = await session.get(Room, room_id)
            assert db_room.status == "ended"
            assert db_room.final_scores_json == final_scores
            assert db_room.ended_at is not None


if __name__ == "__main__":
    # 可以直接运行此文件进行测试
    pytest.main([__file__, "-v"])
