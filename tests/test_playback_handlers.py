import pytest
import random
from cache.schemas import PlaybackState
from client_manager import ClientManager, Client
from schemas.base_message import ErrorMessage, ErrorMessageData
from schemas.ws_messages import playback_schemas
import asyncio
from cache.room_cache import set_room_playback_state, get_room_playback_state
from rich import print


def random_string(length: int = 8, prefix: str = "") -> str:
    """生成随机字符串"""
    letters = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return prefix + ''.join(random.choice(letters) for _ in range(length))


@pytest.mark.asyncio
async def test_handle_play():
    room_id = random_string(prefix="test-room-play-")  # 生成随机房间ID
    data = playback_schemas.PlayMessage(data=playback_schemas.PlayControlData(
        progress_ms=5000,
        offset_ts=1234567890,
        audio_url="https://example.com/audio.mp3"))
    state = PlaybackState.model_validate(data.data)
    await asyncio.gather(
        set_room_playback_state(room_id, state),
        return_exceptions=True,
    )
    print(state, data)


@pytest.mark.asyncio
async def test_handle_pause():
    room_id = random_string(prefix="test-room-pause-")  # 生成随机房间ID
    data = playback_schemas.PauseMessage(data=playback_schemas.PlayControlData(
        progress_ms=5000,
        offset_ts=1234567890,
        audio_url="https://example.com/audio.mp3"))
    state = PlaybackState.model_validate(data.data)
    await asyncio.gather(
        set_room_playback_state(room_id, state),
        return_exceptions=True,
    )
    print(state, data)


@pytest.mark.asyncio
async def test_handle_seek():
    room_id = random_string(prefix="test-room-seek-")  # 生成随机房间ID
    data = playback_schemas.SeekMessage(data=playback_schemas.PlayControlData(
        progress_ms=5000,
        offset_ts=1234567890,
        audio_url="https://example.com/audio.mp3"))
    state = PlaybackState.model_validate(data.data)
    await asyncio.gather(
        set_room_playback_state(room_id, state),
        return_exceptions=True,
    )
    print(state, data)
    assert state.play_state
    data = playback_schemas.SeekMessage(data=playback_schemas.PlayControlData(
        progress_ms=5000,
        offset_ts=1234567890,
        audio_url="https://example.com/audio.mp3"))
    state = PlaybackState.model_validate(data.data)
    await asyncio.gather(
        set_room_playback_state(room_id, state),
        return_exceptions=True,
    )
    print(state, data)
