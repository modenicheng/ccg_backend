from __future__ import annotations

import asyncio
import random

import pytest
from rich import print
from sqlalchemy.ext.asyncio import AsyncSession

from cache.room_cache import (
    get_answer_queue,
    get_room_playback_state,
    set_room_playback_state,
    append_attempt_answer_player,
    delete_room_playback_state,
    clear_answer_queue,
)
from cache.schemas import AnswerQueueItem, PlaybackState
from db import models
from db.crud import get_room_players_cache, update_room_player_online_status


def random_string(length: int = 8, prefix: str = "") -> str:
    """生成随机字符串"""
    letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return prefix + "".join(random.choice(letters) for _ in range(length))


@pytest.mark.asyncio(loop_scope="session")
async def test_room_playback_state_cache():
    """测试房间播放状态缓存"""
    room_id = "test-room-playback-123"
    playback_state = PlaybackState(
        progress_ms=5000,
        play_state="playing",
        current_order=1,
        audio_url="https://example.com/audio.mp3",
    )

    # 保存播放状态到 Redis
    await set_room_playback_state(room_id, playback_state)

    # 从 Redis 获取播放状态
    loaded_state = await get_room_playback_state(room_id)

    # 验证加载的状态与原始状态一致
    assert loaded_state == playback_state

    # 清理测试数据
    await delete_room_playback_state(room_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_room_players_cache(db_session: AsyncSession):
    """测试房间玩家状态（SQL 实现）"""
    room_id = "test-room-players-456"

    # 在数据库中创建房间和玩家
    room = models.Room(id=room_id, title="Test Room")
    db_session.add(room)
    users = [
        models.User(username="Alice",
                    room_id=room_id,
                    is_owner=True,
                    online=True,
                    token="tok-alice"),
        models.User(username="Bob",
                    room_id=room_id,
                    is_owner=False,
                    online=True,
                    token="tok-bob"),
        models.User(username="Charlie",
                    room_id=room_id,
                    is_owner=False,
                    online=False,
                    token="tok-charlie"),
        models.User(username="David",
                    room_id=room_id,
                    is_owner=False,
                    online=True,
                    token="tok-david"),
    ]
    db_session.add_all(users)
    await db_session.flush()

    # 通过 SQL 获取玩家
    cached_players = await get_room_players_cache(room_id, session=db_session)

    # 验证玩家数量和字段
    assert len(cached_players) == len(users)
    names = {p.username for p in cached_players}
    assert names == {"Alice", "Bob", "Charlie", "David"}

    alice = next(p for p in cached_players if p.username == "Alice")
    charlie = next(p for p in cached_players if p.username == "Charlie")
    assert alice.is_owner is True
    assert alice.online is True
    assert charlie.online is False


def random_player_attempt_answer_data() -> list[tuple[int, int]]:
    """生成随机的玩家答题数据"""
    return [
        (i, random.randint(1772296393000, 1772296393000 + 1000000)) for i in range(10)
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_room_answer_queue_cache():
    """测试房间答题队列缓存"""
    room_id = random_string(prefix="test-room-answer-queue-")  # 生成随机房间ID

    player_data = random_player_attempt_answer_data()
    for player_id, ts in player_data:
        await append_attempt_answer_player(
            room_id, AnswerQueueItem(player_id=player_id, offset_ts=ts))

    # 从 Redis 获取答题队列
    cached_queue = await get_answer_queue(room_id)
    sorted_raw = sorted(player_data, key=lambda x: x[1])

    print("原始数据：", player_data)
    print("缓存数据：", cached_queue)
    # 验证答题队列内容
    assert len(cached_queue) == len(player_data)
    for raw, cached in zip(sorted_raw, cached_queue):
        assert raw[0] == cached.player_id

    # 清理测试数据
    await clear_answer_queue(room_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_player_online_status_sql(db_session: AsyncSession):
    """连接异常闪断测试：玩家上线后立刻下线，users.online 最终应为 False"""
    room_id = "test-room-flash-789"

    room = models.Room(id=room_id, title="Flash Test Room")
    db_session.add(room)
    user = models.User(username="Flash",
                       room_id=room_id,
                       is_owner=False,
                       online=False,
                       token="tok-flash")
    db_session.add(user)
    await db_session.flush()
    player_id = user.id

    # 模拟上线
    ok = await update_room_player_online_status(room_id,
                                                player_id,
                                                True,
                                                session=db_session)
    assert ok is True
    # 同一 session identity map — user 对象已被直接修改，无需 refresh
    assert user.online is True

    # 模拟立刻断线（<100ms，无 sleep 模拟竞态）
    ok = await update_room_player_online_status(room_id,
                                                player_id,
                                                False,
                                                session=db_session)
    assert ok is True
    assert user.online is False


@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_answer_queue():
    """多并发抢答测试：5 名玩家同时提交，队列长度正确且按时间戳排序"""
    room_id = random_string(prefix="test-concurrent-answer-")

    base_ts = 1_700_000_000_000
    tasks = [
        append_attempt_answer_player(
            room_id,
            AnswerQueueItem(player_id=i, offset_ts=base_ts + i * 10),
        ) for i in range(5)
    ]
    await asyncio.gather(*tasks)

    queue = await get_answer_queue(room_id)
    assert len(queue) == 5

    # 验证按 offset_ts 升序排列
    for a, b in zip(queue, queue[1:]):
        assert a.offset_ts <= b.offset_ts

    await clear_answer_queue(room_id)
