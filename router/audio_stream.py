"""Secure audio streaming endpoints with temporary token access."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.session import get_db
from db import models
from cache.file_cache import build_file_response
from utils.audio_metadata import ensure_opus_encoded
from utils.audio_token import get_song_id_from_token
from utils import get_logger

logger = get_logger(__name__)

audio_stream_router = APIRouter(prefix="/api/songs", tags=["audio"])


@audio_stream_router.get("/stream/{token}")
async def stream_audio(
        token: str,
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
        ensure_opus_encoded(song.cached_path)
        return build_file_response(song.cached_path)
    except HTTPException:
        raise
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error loading audio file: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load audio") from e  # pylint: disable=raise-missing-from


@audio_stream_router.get("/file/{song_id}")
async def get_audio_file(
        song_id: int,
        db: AsyncSession = Depends(get_db),
):
    """Stream audio file for a song (compatible with audio tag)."""
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

    try:
        ensure_opus_encoded(song.cached_path)
        content_disposition = (
            f'inline; filename="{os.path.basename(song.cached_path)}"')
        return build_file_response(song.cached_path, content_disposition)
    except HTTPException:
        raise
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error loading audio file for streaming: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load audio") from e
