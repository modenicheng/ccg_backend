"""WebSocket event handler for audio preload errors reported by the frontend."""
from __future__ import annotations

from sqlalchemy import select

from client_manager import ClientManager, Client
from db import models
from db.session import session_scope
from handlers.audio_common import broadcast_preload_audio_for_index
from mq import tasks
from schemas.ws_messages.playback_schemas import AudioErrorMessage
from utils import get_logger
from utils.enumerations import EventType
from .registe_manager import regist

logger = get_logger(__name__)


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
        client.sid,
        error_type,
        reason,
        audio_url,
    )

    try:
        # Get current room and song queue information from database
        async with session_scope() as session:
            room_stmt = select(models.Room).where(models.Room.id == room_id)
            room_result = await session.execute(room_stmt)
            room = room_result.scalar_one_or_none()

            if not room:
                logger.error(
                    "[AUDIO_ERROR] Room %s not found in database",
                    room_id,
                )
                return

            # Get song queue and current index
            room_song_queue = room.room_song_queue or []
            current_song_queue_index = room.current_song_queue_index or 0

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

            # Re-broadcast PRELOAD_AUDIO message with refreshed URL/token
            try:
                await broadcast_preload_audio_for_index(
                    clients=clients,
                    session=session,
                    room_id=room_id,
                    song_queue=room_song_queue,
                    index=current_song_queue_index,
                )
                logger.info(
                    "[AUDIO_ERROR] Re-broadcasted PRELOAD_AUDIO for song %s in room %s",
                    current_song_id,
                    room_id,
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error(
                    "[AUDIO_ERROR] Failed to re-broadcast PRELOAD_AUDIO for song %s: %s",
                    current_song_id,
                    e,
                    exc_info=True,
                )

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(
            "[AUDIO_ERROR] Exception handling audio error for room %s: %s",
            room_id,
            e,
            exc_info=True,
        )
