from __future__ import annotations
"""
安全音频流端点
通过临时令牌访问音频，保护音频ID不被直接暴露
"""
import datetime

import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db
from db import crud
from cache.file_cache import load_song_asset_with_cache
from utils.audio_token import get_song_id_from_token
from utils import get_logger
from utils.http_utils import build_range_response

logger = get_logger(__name__)

audio_stream_router = APIRouter(prefix="/api/songs", tags=["audio"])


def _utc_now_naive() -> datetime.datetime:
    """返回naive UTC时间，匹配数据库DateTime(timezone=False)字段。"""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


@audio_stream_router.get("/stream/{token}")
async def stream_audio(
        token: str,
        request: Request,
        session: AsyncSession = Depends(get_db),
) -> Response:
    """
    安全音频流端点
    验证令牌并返回音频文件

    Args:
        token: 音频访问令牌
        request: FastAPI请求对象
        session: 数据库会话

    Returns:
        Response: 音频文件响应（支持Range请求）
    """
    # 1. 解析 token 中的 song_id（新token可解析，旧token可为空）
    logger.info(f"Validating audio token: {token[:8]}...")
    token_song_id = await get_song_id_from_token(token)

    if token_song_id:
        logger.info(f"Audio token parsed, song_id: {token_song_id}")
    else:
        logger.info(
            f"Audio token {token[:8]}... cannot be parsed directly, fallback to temp_url lookup"
        )

    # 2. 通过已落库 temp_url 反查 RoomSong + Song
    room_song_with_song = await crud.get_room_song_and_song_by_temp_token(
        session, token)
    if not room_song_with_song:
        logger.warning(f"Audio token {token[:8]}... not bound to any room")
        # 如果token可解析，尝试通过song_id查找RoomSong作为回退
        if token_song_id:
            from sqlalchemy import select
            from db import models

            stmt = (select(models.RoomSong, models.Song).join(
                models.Song, models.Song.id == models.RoomSong.song_id).where(
                    models.RoomSong.song_id == token_song_id))
            result = await session.execute(stmt)
            fallback_row = result.first()
            if fallback_row:
                room_song, song = fallback_row
                logger.info(
                    f"Fallback found RoomSong for song_id {token_song_id}, room_id {room_song.room_id}"
                )
                # 检查token是否过期
                if room_song.expire_at and room_song.expire_at < _utc_now_naive():
                    logger.warning(
                        f"Audio token {token[:8]}... expired at {room_song.expire_at}")
                    raise HTTPException(status_code=403, detail="Audio token expired")
                room_song_with_song = (room_song, song)
            else:
                logger.warning(f"No RoomSong found for song_id {token_song_id}")
        if not room_song_with_song:
            raise HTTPException(status_code=403,
                                detail="Audio token not valid for any room")

    room_song, song = room_song_with_song

    # 3. 若token可解析，则校验解析结果与落库记录一致
    if token_song_id and room_song.song_id != token_song_id:
        logger.warning(
            f"Audio token {token[:8]}... song_id mismatch: token={token_song_id}, room_song={room_song.song_id}"
        )
        raise HTTPException(status_code=403, detail="Audio token mismatch")

    # 4. 检查token是否过期
    if room_song.expire_at and room_song.expire_at < _utc_now_naive():
        logger.warning(f"Audio token {token[:8]}... expired at {room_song.expire_at}")
        raise HTTPException(status_code=403, detail="Audio token expired")

    if not song.cached_path:
        logger.error(f"Song {room_song.song_id} has no cached path")
        raise HTTPException(status_code=404, detail="Audio file not cached")

    # 5. 返回音频文件（支持Range请求）
    content, media_type = await load_song_asset_with_cache(song.cached_path)
    range_header = request.headers.get("range")

    response = build_range_response(content, media_type, range_header)

    # 添加安全头和缓存头
    response.headers["Cache-Control"] = "private, max-age=3600"  # 客户端缓存1小时
    response.headers["Content-Disposition"] = (
        f'inline; filename="{os.path.basename(song.cached_path)}"')

    logger.info(f"Successfully served audio for song_id: {room_song.song_id}")
    return response
