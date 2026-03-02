import random

import pytest
from cache.room_cache import *
from cache.schemas import *

from rich import print


def random_string(length: int = 8, prefix: str = "") -> str:
    """生成随机字符串"""
    letters = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return prefix + ''.join(random.choice(letters) for _ in range(length))


@pytest.mark.asyncio(loop_scope="session")
async def test_room_playback_state_cache():
    """测试房间播放状态缓存"""
    room_id = "test-room-playback-123"
    playback_state = PlaybackState(progress_ms=5000,
                                   play_state="playing",
                                   current_order=1,
                                   audio_url="https://example.com/audio.mp3")

    # 保存播放状态到 Redis
    await set_room_playback_state(room_id, playback_state)

    # 从 Redis 获取播放状态
    loaded_state = await get_room_playback_state(room_id)

    # 验证加载的状态与原始状态一致
    assert loaded_state == playback_state

    # 清理测试数据
    await delete_room_playback_state(room_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_room_players_cache():
    """测试房间玩家缓存"""
    room_id = "test-room-players-456"
    players = [
        RoomStatePlayerItem(id=1, username="Alice", is_owner=True,
                            online=True),
        RoomStatePlayerItem(id=2, username="Bob", is_owner=False, online=True),
        RoomStatePlayerItem(id=3,
                            username="Charlie",
                            is_owner=False,
                            online=False),
        RoomStatePlayerItem(id=4,
                            username="David",
                            is_owner=False,
                            online=True),
    ]

    try:
        # 保存玩家到 Redis
        await save_room_players(room_id, players)

        # 从 Redis 获取玩家
        cached_players = await get_room_players(room_id)

        # 验证玩家数量
        assert len(cached_players) == len(players)

        # 验证每个玩家都在缓存中
        for p in players:
            assert p in cached_players

    finally:
        # 清理测试数据
        await delete_room_players(room_id)


def random_player_attempt_answer_data() -> list[tuple[int, int]]:
    """生成随机的玩家答题数据"""
    return [(i, random.randint(1772296393000, 1772296393000 + 1000000))
            for i in range(10)]


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
