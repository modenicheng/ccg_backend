"""Audio pre-download common functions for WebSocket handlers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import mq.tasks
from client_manager import ClientManager
from db import models
from db.crud import get_or_create_audio_token
from utils import get_logger

logger = get_logger(__name__)


async def download_song_at_index(
    session: AsyncSession,
    room_id: str,
    song_queue: list[int],
    index: int,
) -> int | None:
    """下载指定索引的歌曲（如果平台是QQ）。

    Args:
        session: 数据库会话
        room_id: 房间ID
        song_queue: 歌曲ID队列
        index: 歌曲索引

    Returns:
        成功下载返回歌曲ID，否则返回None
    """
    if index >= len(song_queue):
        return None

    song_id = song_queue[index]
    song_stmt = select(models.Song).where(models.Song.id == song_id)
    song_result = await session.execute(song_stmt)
    song = song_result.scalar_one_or_none()
    if not song:
        logger.warning(
            "Cannot preload song %s in room %s: song not found",
            song_id,
            room_id,
        )
        return None

    if song.platform != "qq" or not song.platform_song_id:
        return None

    try:
        mq.tasks.download_and_cache_song(str(song.platform_song_id))
        logger.info(
            "Triggered preload task for song %s (index %s) in room %s",
            song_id,
            index,
            room_id,
        )
        # 预下载时提前生成/刷新临时token，确保RoomSong.temp_url可用
        await get_or_create_audio_token(session, room_id, song_id)
        return song_id
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Failed to trigger preload task for song %s in room %s: %s",
            song_id,
            room_id,
            e,
        )
        return None


async def preload_songs_for_round_start(
    clients: ClientManager,
    session: AsyncSession,
    room_id: str,
    song_queue: list[int],
    current_index: int,
) -> None:
    """ROUND_START 时的预下载逻辑。

    根据规定：
    - 预下载：下载 i+3 歌曲

    Args:
        clients: WebSocket客户端管理器
        session: 数据库会话
        room_id: 房间ID
        song_queue: 歌曲ID队列
        current_index: 当前回合索引 (i)
    """
    # 预下载 i+3 歌曲
    download_index = current_index + 3
    if download_index < len(song_queue):
        await download_song_at_index(session, room_id, song_queue, download_index)
