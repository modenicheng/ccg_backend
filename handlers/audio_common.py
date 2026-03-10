"""Audio preload common functions for WebSocket handlers."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import mq.tasks
from client_manager import ClientManager
from db import models
from db.crud import get_or_create_audio_token
from schemas.ws_messages.playback_schemas import PlayControlData, PreloadAudioMessage
from utils import get_logger
from utils.audio_token import get_audio_stream_url

logger = get_logger(__name__)


async def preload_and_broadcast_audio(
    session: AsyncSession,
    room_id: str,
    song_id: int,
    clients: ClientManager,
    preload_index: Optional[int] = None,
) -> None:
    """
    预加载音频并广播PRELOAD_AUDIO消息

    Args:
        session: 数据库会话
        room_id: 房间ID
        song_id: 歌曲ID
        clients: WebSocket客户端管理器
        preload_index: 预加载索引（用于日志记录）

    Raises:
        Exception: 如果音频token生成或广播失败
    """
    # 获取或创建音频token
    audio_token = await get_or_create_audio_token(session, room_id, song_id)

    # 生成音频流URL
    audio_url = get_audio_stream_url(audio_token)

    # 创建预加载消息
    message = PreloadAudioMessage(data=PlayControlData(audio_url=audio_url))

    # 广播消息
    await clients.broadcast(room_id, message.model_dump())

    # 记录日志
    index_info = f" (index {preload_index})" if preload_index is not None else ""
    logger.info("Broadcast PRELOAD_AUDIO for song %s%s in room %s", song_id, index_info,
                room_id)


async def trigger_and_broadcast_preload_for_index(
    clients: ClientManager,
    session: AsyncSession,
    room_id: str,
    song_queue: list[int],
    preload_index: int,
) -> None:
    """触发指定索引歌曲预下载并广播 PRELOAD_AUDIO。"""
    if preload_index >= len(song_queue):
        return

    preload_song_id = song_queue[preload_index]
    song_stmt = select(models.Song).where(models.Song.id == preload_song_id)
    song_result = await session.execute(song_stmt)
    song = song_result.scalar_one_or_none()
    if not song:
        logger.warning(
            "Cannot preload song %s in room %s: song not found",
            preload_song_id,
            room_id,
        )
        return

    if song.platform != "qq" or not song.platform_song_id:
        return

    try:
        mq.tasks.download_and_cache_song(str(song.platform_song_id))
        logger.info(
            "Triggered preload task for song %s (index %s) in room %s",
            preload_song_id,
            preload_index,
            room_id,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Failed to trigger preload task for song %s in room %s: %s",
            preload_song_id,
            room_id,
            e,
        )
        return

    try:
        await preload_and_broadcast_audio(session, room_id, preload_song_id, clients,
                                          preload_index)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Failed to broadcast PRELOAD_AUDIO for song %s in room %s: %s",
            preload_song_id,
            room_id,
            e,
        )
