"""
安全音频流端点
通过临时令牌访问音频，保护音频ID不被直接暴露
"""

import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.session import get_db
from db.models import Song
from cache.file_cache import load_song_asset_with_cache
from utils.audio_token import get_song_id_from_token, get_audio_stream_url
from utils import get_logger

logger = get_logger(__name__)

audio_stream_router = APIRouter(prefix="/api/songs", tags=["audio"])


def _build_range_response(content: bytes, media_type: str,
                          range_header: str | None) -> Response:
    """
    构建支持Range请求的响应
    从song.py复制，确保一致性
    """
    total = len(content)
    common_headers = {
        "Accept-Ranges": "bytes",
    }

    if not range_header:
        return Response(content=content,
                        media_type=media_type,
                        headers={
                            **common_headers, "Content-Length": str(total)
                        })

    if not range_header.startswith("bytes="):
        raise HTTPException(status_code=416, detail="Invalid Range header")

    range_spec = range_header.replace("bytes=", "", 1).strip()
    if "," in range_spec:
        raise HTTPException(status_code=416,
                            detail="Multiple ranges are not supported")

    start_str, sep, end_str = range_spec.partition("-")
    if sep != "-":
        raise HTTPException(status_code=416, detail="Invalid Range header")

    try:
        if start_str == "":
            # suffix-byte-range-spec: bytes=-500 (最后500字节)
            suffix_length = int(end_str)
            if suffix_length <= 0:
                raise ValueError
            start = max(total - suffix_length, 0)
            end = total - 1
        else:
            start = int(start_str)
            if end_str == "":
                end = total - 1
            else:
                end = int(end_str)
    except ValueError as e:
        raise HTTPException(status_code=416,
                            detail="Invalid Range header") from e

    if total == 0 or start < 0 or end < start or start >= total:
        return Response(status_code=416,
                        headers={
                            "Content-Range": f"bytes */{total}",
                            **common_headers
                        })

    end = min(end, total - 1)
    partial = content[start:end + 1]
    return Response(content=partial,
                    status_code=206,
                    media_type=media_type,
                    headers={
                        **common_headers, "Content-Range":
                        f"bytes {start}-{end}/{total}",
                        "Content-Length": str(len(partial))
                    })


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
    # 1. 验证令牌
    logger.info(f"Validating audio token: {token[:8]}...")
    song_id = await get_song_id_from_token(token)
    
    if not song_id:
        logger.warning(f"Invalid or expired audio token: {token[:8]}...")
        raise HTTPException(status_code=403, detail="Invalid or expired audio token")
    
    logger.info(f"Audio token valid, song_id: {song_id}")
    
    # 2. 获取歌曲文件
    stmt = select(Song).where(Song.id == song_id)
    result = await session.execute(stmt)
    song = result.scalar_one_or_none()
    
    if not song:
        logger.error(f"Song not found for song_id: {song_id}")
        raise HTTPException(status_code=404, detail="Audio not found")
    
    if not song.cached_path:
        logger.error(f"Song {song_id} has no cached path")
        raise HTTPException(status_code=404, detail="Audio file not cached")
    
    # 3. 返回音频文件（支持Range请求）
    content, media_type = await load_song_asset_with_cache(song.cached_path)
    range_header = request.headers.get("range")
    
    response = _build_range_response(content, media_type, range_header)
    
    # 添加安全头和缓存头
    response.headers["Cache-Control"] = "private, max-age=3600"  # 客户端缓存1小时
    response.headers["Content-Disposition"] = (
        f'inline; filename="{os.path.basename(song.cached_path)}"')
    
    logger.info(f"Successfully served audio for song_id: {song_id}")
    return response