"""Secure audio streaming endpoints with temporary token access."""

from __future__ import annotations

import datetime
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.session import get_db
from db import models
from cache.file_cache import load_song_asset_with_cache
from utils.audio_token import get_song_id_from_token
from utils import get_logger
from utils.http_utils import build_range_response

logger = get_logger(__name__)

audio_stream_router = APIRouter(prefix="/api/songs", tags=["audio"])


def _utc_now_naive() -> datetime.datetime:
    """Return naive UTC time matching database DateTime(timezone=False) field."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


@audio_stream_router.get("/stream/{token}")
async def stream_audio(
        token: str,
        request: Request,
        db: AsyncSession = Depends(get_db),
):
    """Stream audio file using temporary token."""
    logger.debug("Audio stream request for token: %s", token)

    song_id = await get_song_id_from_token(token)
    if not song_id:
        logger.warning("Invalid audio token: %s", token)
        raise HTTPException(status_code=404, detail="Audio not found")

    stmt = select(models.Song).where(models.Song.id == song_id)
    result = await db.execute(stmt)
    song = result.scalar_one_or_none()

    if not song:
        logger.warning("Song not found for id: %s", song_id)
        raise HTTPException(status_code=404, detail="Audio not found")

    if not song.cached_path:
        logger.warning("Audio not cached for song id: %s", song_id)
        raise HTTPException(status_code=404, detail="Audio not available")

    try:
        content, media_type = await load_song_asset_with_cache(song.cached_path)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error loading audio file: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load audio") from e  # pylint: disable=raise-missing-from

    file_size = len(content)
    range_header = request.headers.get("range")

    if range_header:
        return build_range_response(content, media_type, range_header)

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


@audio_stream_router.get("/file/{song_id}")
async def get_audio_file(
        song_id: int,
        db: AsyncSession = Depends(get_db),
):
    """Get audio file path for a song (requires ownership check)."""
    logger.debug("Audio file request for song id: %s", song_id)

    song = await db.get(models.Song, song_id)
    if not song:
        logger.warning("Song not found for id: %s", song_id)
        raise HTTPException(status_code=404, detail="Song not found")

    if not song.cached_path:
        logger.warning("Audio not cached for song id: %s", song_id)
        raise HTTPException(status_code=404, detail="Audio not available")

    if not os.path.exists(song.cached_path):
        logger.warning("Audio file missing on disk: %s", song.cached_path)
        raise HTTPException(status_code=404, detail="Audio file not found")

    return {
        "song_id": song.id,
        "file_path": song.cached_path,
        "file_exists": True,
    }
