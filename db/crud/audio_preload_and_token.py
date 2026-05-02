"""Audio preload and token management functions."""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import app_config
from utils import get_logger
from utils.audio_token import generate_audio_token, get_song_id_from_token

from .. import models
from .judge_related import get_room_song_queue

logger = get_logger(__name__)


def _utc_now_naive() -> datetime.datetime:
    """返回naive UTC时间，匹配PostgreSQL timestamp without time zone列。"""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


async def get_or_create_audio_token(
    session: AsyncSession,
    room_id: str,
    song_id: int,
) -> str:
    """
    获取或为房间歌曲创建音频token

    Args:
        session: 数据库会话
        room_id: 房间ID
        song_id: 歌曲ID

    Returns:
        str: 音频访问token
    """
    # 查询RoomSong记录

    stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id,
                                         models.RoomSong.song_id == song_id)
    result = await session.execute(stmt)
    room_song = result.scalar_one_or_none()

    if not room_song:
        logger.warning("RoomSong not found for room %s, song %s", room_id, song_id)
        # 如果RoomSong不存在，创建新的token但不存储（理论上不应该发生）
        token = await generate_audio_token(song_id)
        return token

    current_time = _utc_now_naive()

    # 检查现有token是否有效（DB未过期 + Redis映射有效且song_id一致）
    if room_song.temp_url and room_song.expire_at:
        if room_song.expire_at > current_time:
            existing_song_id = await get_song_id_from_token(room_song.temp_url)
            if existing_song_id == song_id:
                logger.debug("Using existing token for room %s, song %s", room_id,
                             song_id)
                return room_song.temp_url
            logger.info(
                "Existing token in DB is not resolvable in Redis or song_id mismatch "
                "for room %s, song %s; regenerating",
                room_id,
                song_id,
            )
        else:
            logger.debug("Token expired for room %s, song %s", room_id, song_id)

    # 生成新token
    token = await generate_audio_token(song_id)
    expire_at = current_time + datetime.timedelta(seconds=app_config.audio_token_ttl)

    # 更新RoomSong记录
    room_song.temp_url = token
    room_song.expire_at = expire_at

    logger.info(
        "Created new audio token for room %s, song %s, expires at %s",
        room_id,
        song_id,
        expire_at,
    )
    return token


async def validate_audio_token(
    session: AsyncSession,
    room_id: str,
    token: str,
) -> bool:
    """
    验证音频token是否对房间有效

    Args:
        session: 数据库会话
        room_id: 房间ID
        token: 音频访问token

    Returns:
        bool: token是否有效
    """
    # 从token获取song_id
    song_id = await get_song_id_from_token(token)
    if not song_id:
        logger.debug("Invalid token or token expired: %s...", token[:8])
        return False

    # 查询RoomSong记录

    stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id,
                                         models.RoomSong.song_id == song_id)
    result = await session.execute(stmt)
    room_song = result.scalar_one_or_none()

    if not room_song:
        logger.debug("RoomSong not found for room %s, song %s", room_id, song_id)
        return False

    current_time = _utc_now_naive()

    # 验证token和过期时间
    if room_song.temp_url != token:
        logger.debug("Token mismatch for room %s, song %s", room_id, song_id)
        return False

    if not room_song.expire_at or room_song.expire_at <= current_time:
        logger.debug("Token expired for room %s, song %s", room_id, song_id)
        return False

    logger.debug("Token valid for room %s, song %s", room_id, song_id)
    return True


async def update_room_song_temp_token(
    session: AsyncSession,
    room_id: str,
    song_id: int,
    token: str,
) -> None:
    """
    更新RoomSong的临时token

    Args:
        session: 数据库会话
        room_id: 房间ID
        song_id: 歌曲ID
        token: 音频访问token
    """

    stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id,
                                         models.RoomSong.song_id == song_id)
    result = await session.execute(stmt)
    room_song = result.scalar_one_or_none()

    if not room_song:
        logger.warning(
            "Cannot update token: RoomSong not found for room %s, song %s",
            room_id,
            song_id,
        )
        return

    current_time = _utc_now_naive()
    expire_at = current_time + datetime.timedelta(seconds=app_config.audio_token_ttl)

    room_song.temp_url = token
    room_song.expire_at = expire_at

    logger.debug("Updated token for room %s, song %s, expires at %s", room_id, song_id,
                 expire_at)


async def prepare_preload_songs(  # pylint: disable=too-many-locals
    session: AsyncSession,
    room_id: str,
    start_index: int = 0,
    count: int = 3,
) -> list[str]:
    """准备歌曲预下载所需信息并刷新临时 token。

    该函数只负责数据读取与 token 刷新，不触发任何异步任务。

    Returns:
        list[str]: 需要预下载的 QQ 歌曲 platform_song_id 列表（按队列顺序）
    """
    song_queue = await get_room_song_queue(session, room_id)
    if not song_queue:
        logger.debug("No songs in room %s to prepare preload", room_id)
        return []

    end_index = min(start_index + count, len(song_queue))
    if start_index >= end_index:
        logger.debug(
            "Invalid preload range: start_index=%s, end_index=%s",
            start_index,
            end_index,
        )
        return []

    target_song_ids = song_queue[start_index:end_index]
    stmt = select(models.Song).where(models.Song.id.in_(target_song_ids))
    result = await session.execute(stmt)
    songs = result.scalars().all()
    songs_by_id = {song.id: song for song in songs}

    platform_song_ids: list[str] = []
    token_updated_count = 0

    for song_id in target_song_ids:
        song = songs_by_id.get(song_id)
        if not song:
            logger.warning(
                "Song %s not found while preparing preload for room %s",
                song_id,
                room_id,
            )
            continue

        if song.platform != "qq" or not song.platform_song_id:
            continue

        try:
            await get_or_create_audio_token(session, room_id, song.id)
            token_updated_count += 1
            platform_song_ids.append(str(song.platform_song_id))
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Failed to refresh preload token for room %s, song %s: %s",
                room_id,
                song.id,
                e,
            )

    await session.flush()

    logger.info(
        "Prepared preload for %s songs and updated tokens for %s songs "
        "in room %s (range %s-%s)",
        len(platform_song_ids),
        token_updated_count,
        room_id,
        start_index,
        end_index - 1,
    )
    return platform_song_ids


async def get_room_song_by_song_id(
    session: AsyncSession,
    room_id: str,
    song_id: int,
) -> models.RoomSong | None:
    """
    根据房间ID和歌曲ID获取RoomSong记录

    Args:
        session: 数据库会话
        room_id: 房间ID
        song_id: 歌曲ID

    Returns:
        RoomSong记录或None
    """

    stmt = select(models.RoomSong).where(models.RoomSong.room_id == room_id,
                                         models.RoomSong.song_id == song_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_room_song_and_song_by_temp_token(
    session: AsyncSession,
    token: str,
) -> tuple[models.RoomSong, models.Song] | None:
    """根据已落库的 RoomSong.temp_url 反查 RoomSong 与 Song。"""
    stmt = (select(models.RoomSong,
                   models.Song).join(models.Song,
                                     models.Song.id == models.RoomSong.song_id).where(
                                         models.RoomSong.temp_url == token))
    result = await session.execute(stmt)
    row = result.first()
    if not row:
        return None
    room_song, song = row
    return room_song, song


async def update_room_current_song_index(
    session: AsyncSession,
    room_id: str,
    song_index: int,
) -> None:
    """
    更新房间的当前歌曲索引

    Args:
        session: 数据库会话
        room_id: 房间ID
        song_index: 新的歌曲索引
    """

    stmt = select(models.Room).where(models.Room.id == room_id)
    result = await session.execute(stmt)
    room = result.scalar_one_or_none()

    if not room:
        logger.warning("Room %s not found", room_id)
        return

    room.current_song_index = song_index
    logger.debug("Updated room %s current_song_index to %s", room_id, song_index)
