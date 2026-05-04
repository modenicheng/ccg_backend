"""WebSocket event handler for audio errors reported by the frontend."""
from __future__ import annotations

import time

from sqlalchemy import select

from client_manager import ClientManager, Client
from db import models
from db.crud import get_current_song_info, get_room_song_queue
from db.session import session_scope
from mq import tasks
from schemas.ws_messages.playback_schemas import AudioErrorMessage
from utils import get_logger
from utils.enumerations import EventType
from .registe_manager import regist

logger = get_logger(__name__)

_REDOWNLOAD_COOLDOWN_SECONDS = 30
_last_redownload: dict[str, float] = {}  # room_id -> last re-download timestamp


@regist(EventType.ERROR, data_validator=AudioErrorMessage)
async def handle_audio_preload_error(
    data: AudioErrorMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    """
    Handle audio preload errors reported by the frontend.

    When frontend fails to preload/play audio, attempt to:
    1. Re-download the audio file from scratch
    2. Re-broadcast the PRELOAD_AUDIO message with refreshed URL/token

    Args:
        data: AudioErrorMessage containing error_type, reason, and audio_url
        clients: WebSocket client manager for broadcasting
        client: The client that reported the error
        room_id: The room where the error occurred
    """
    error_type = data.data.error_type
    reason = data.data.reason
    audio_url = data.data.audio_url or "unknown"

    logger.warning(
        "[AUDIO_ERROR] Room %s, Client %s reported %s error: %s (URL: %s)",
        room_id,
        client.user_id,
        error_type,
        reason,
        audio_url,
    )

    now = time.monotonic()
    last_time = _last_redownload.get(room_id, 0)
    if now - last_time < _REDOWNLOAD_COOLDOWN_SECONDS:
        logger.info(
            "[AUDIO_ERROR] Rate limited re-download for room %s (cooldown %ds remaining)",
            room_id,
            int(_REDOWNLOAD_COOLDOWN_SECONDS - (now - last_time)),
        )
        return
    _last_redownload[room_id] = now

    try:
        # Get current room and song queue information from database
        async with session_scope() as session:
            current_song_id, current_song_queue_index = await get_current_song_info(
                session,
                room_id,
            )
            if current_song_id is None or current_song_queue_index is None:
                logger.error(
                    "[AUDIO_ERROR] Room %s not found or current song unavailable in database",
                    room_id,
                )
                return

            # Get song queue and current index
            room_song_queue = await get_room_song_queue(session, room_id)

            if not room_song_queue or current_song_queue_index >= len(room_song_queue):
                logger.warning(
                    "[AUDIO_ERROR] Invalid song queue for room %s, index: %s, queue length: %s",
                    room_id,
                    current_song_queue_index,
                    len(room_song_queue),
                )
                return

            current_song_id = room_song_queue[current_song_queue_index]

            # Fetch song details from database
            song_stmt = select(models.Song).where(models.Song.id == current_song_id)
            song_result = await session.execute(song_stmt)
            song = song_result.scalar_one_or_none()

            if not song:
                logger.error(
                    "[AUDIO_ERROR] Song %s not found in database for room %s",
                    current_song_id,
                    room_id,
                )
                return

            if song.platform != "qq" or not song.platform_song_id:
                logger.warning(
                    "[AUDIO_ERROR] Song %s (platform: %s) does not support re-download",
                    current_song_id,
                    song.platform,
                )
                return

            logger.info(
                "[AUDIO_ERROR] Attempting to re-download song %s (MID: %s) for room %s",
                current_song_id,
                song.platform_song_id,
                room_id,
            )

            # Trigger re-download task
            try:
                tasks.download_and_cache_song(str(song.platform_song_id))
                logger.info(
                    "[AUDIO_ERROR] Re-download task triggered for song %s (MID: %s) in room %s",
                    current_song_id,
                    song.platform_song_id,
                    room_id,
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error(
                    "[AUDIO_ERROR] Failed to trigger re-download task for song %s: %s",
                    current_song_id,
                    e,
                    exc_info=True,
                )
                return

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(
            "[AUDIO_ERROR] Exception handling audio error for room %s: %s",
            room_id,
            e,
            exc_info=True,
        )
