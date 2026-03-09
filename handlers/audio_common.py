from __future__ import annotations
"""
音频预加载通用函数
提供WebSocket音频预加载和广播的共享逻辑
"""

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from db.crud import get_or_create_audio_token
from schemas.ws_messages.playback_schemas import PreloadAudioMessage, PlayControlData
from utils.audio_token import get_audio_stream_url
from utils import get_logger
from client_manager import ClientManager

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
    logger.info(
        f"Broadcast PRELOAD_AUDIO for song {song_id}{index_info} in room {room_id}")
