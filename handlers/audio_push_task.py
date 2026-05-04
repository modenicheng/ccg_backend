"""WebSocket binary audio push engine.

Reads Ogg/Opus files, extracts raw Opus packets, and pushes them as
AudioFrame binary messages over WebSocket at ~1.5x playback speed.
"""
from __future__ import annotations

import asyncio
import struct
from typing import Optional

from sqlalchemy import select

from client_manager import ClientManager
from db import models
from db.session import session_scope
from utils import get_logger, get_song_id_from_token
from utils.audio_metadata import ensure_opus_encoded
from utils.dataframe import AudioFrame
from utils.enumerations import AudioEncoding

logger = get_logger(__name__)

# Opus at 48kHz: each frame = 960 samples = 20ms
OPUS_FRAME_DURATION_MS = 20.0
# Push at 1.5x speed: 20ms / 1.5 = 13.33ms between frames
PUSH_INTERVAL_SEC = OPUS_FRAME_DURATION_MS / 1000.0 / 1.5


def _parse_ogg_opus_packets(data: bytes) -> list[bytes]:
    """Parse an Ogg/Opus file and extract raw Opus packets.

    Skips the first two Ogg pages (OpusHead and OpusTags headers).
    Returns a list of raw Opus packet bytes from subsequent pages.
    """
    packets: list[bytes] = []
    offset = 0
    page_index = 0

    while offset < len(data):
        # Need at least 27 bytes for Ogg page header
        if offset + 27 > len(data):
            break

        # Verify capture pattern
        if data[offset:offset + 4] != b"OggS":
            logger.warning("Invalid Ogg page at offset %d", offset)
            break

        header_type = data[offset + 5]
        _granule_pos = struct.unpack_from("<Q", data, offset + 6)[0]
        _serial = struct.unpack_from("<I", data, offset + 14)[0]
        _seq = struct.unpack_from("<I", data, offset + 18)[0]
        _checksum = struct.unpack_from("<I", data, offset + 22)[0]
        num_segments = data[offset + 26]

        # Segment table starts at offset + 27
        seg_table_start = offset + 27
        seg_table_end = seg_table_start + num_segments
        if seg_table_end > len(data):
            break

        segment_table = data[seg_table_start:seg_table_end]

        # Calculate total page data size from segment table
        page_data_size = sum(segment_table)
        page_data_start = seg_table_end

        if page_data_start + page_data_size > len(data):
            break

        page_data = data[page_data_start:page_data_start + page_data_size]

        # Skip first two pages (OpusHead and OpusTags)
        if page_index >= 2:
            # Extract packets from this page.
            # Segments with value 255 belong to the same packet;
            # a segment < 255 ends the current packet.
            packet_buf = bytearray()
            seg_offset = 0
            for seg_len in segment_table:
                packet_buf.extend(page_data[seg_offset:seg_offset + seg_len])
                seg_offset += seg_len
                if seg_len < 255:
                    # End of packet
                    if packet_buf:
                        packets.append(bytes(packet_buf))
                    packet_buf = bytearray()

            # If the last segment was 255, flush remaining
            if packet_buf:
                packets.append(bytes(packet_buf))

        # Continue header flag: bit 0 means continued packets
        # (we don't need special handling since we concatenate segments)
        page_index += 1
        offset = page_data_start + page_data_size

    return packets


async def _resolve_audio_path(room_id: str) -> Optional[tuple[str, str]]:
    """Resolve current song's cached_path and audio token from playback state.

    Returns (cached_path, audio_token) or None if unresolvable.
    """
    from cache.room_cache import get_room_playback_state  # pylint: disable=import-outside-toplevel

    state = await get_room_playback_state(room_id)
    if not state or not state.audio_url:
        return None

    # Extract token from URL: /api/songs/stream/{token}
    url = state.audio_url
    token = url.rstrip("/").split("/")[-1]
    if not token:
        return None

    song_id = await get_song_id_from_token(token)
    if song_id is None:
        return None

    async with session_scope() as session:
        song = (await
                session.execute(select(models.Song).where(models.Song.id == song_id)
                                )).scalar_one_or_none()
        if not song or not song.cached_path:
            return None

        cached_path = song.cached_path

    if not ensure_opus_encoded(cached_path):
        logger.error("Failed to ensure opus encoding for %s", cached_path)
        return None

    return cached_path, token


class AudioPushManager:
    """Manages per-room audio push tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    async def start_push(
        self,
        room_id: str,
        song_id_token: str,
        cached_path: str,
        start_ms: int,
        clients: ClientManager,
    ) -> None:
        """Start pushing audio frames for a room's current song."""
        await self.stop_push(room_id)

        cancel_event = asyncio.Event()
        self._cancel_events[room_id] = cancel_event

        task = asyncio.create_task(
            self._push_loop(
                room_id,
                song_id_token,
                cached_path,
                start_ms,
                clients,
                cancel_event,
            ))
        self._tasks[room_id] = task
        task.add_done_callback(lambda t: self._on_task_done(room_id, t))

    async def stop_push(self, room_id: str) -> None:
        """Stop the current push task for a room."""
        if room_id in self._cancel_events:
            self._cancel_events[room_id].set()
        if room_id in self._tasks:
            task = self._tasks.pop(room_id)
            self._cancel_events.pop(room_id, None)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def is_pushing(self, room_id: str) -> bool:
        """Check if audio is currently being pushed for a room."""
        task = self._tasks.get(room_id)
        return task is not None and not task.done()

    def _on_task_done(self, room_id: str, task: asyncio.Task[None]) -> None:
        """Clean up when a push task completes."""
        self._tasks.pop(room_id, None)
        self._cancel_events.pop(room_id, None)
        if task.exception():
            logger.error(
                "Audio push task for room %s failed: %s",
                room_id,
                task.exception(),
            )

    async def _push_loop(
        self,
        room_id: str,
        song_id_token: str,
        cached_path: str,
        start_ms: int,
        clients: ClientManager,
        cancel_event: asyncio.Event,
    ) -> None:
        """Read Opus frames and push at 1.5x speed."""
        try:
            with open(cached_path, "rb") as f:
                file_data = f.read()
        except FileNotFoundError:
            logger.error("Audio file not found: %s", cached_path)
            return
        except OSError:
            logger.error("Failed to read audio file: %s", cached_path)
            return

        packets = _parse_ogg_opus_packets(file_data)
        if not packets:
            logger.warning("No Opus packets found in %s", cached_path)
            return

        # Calculate which packet to start from based on start_ms
        # Each packet = 20ms at 48kHz
        start_packet_idx = max(0, int(start_ms / OPUS_FRAME_DURATION_MS))
        if start_packet_idx >= len(packets):
            logger.warning(
                "Start index %d exceeds packet count %d for room %s",
                start_packet_idx,
                len(packets),
                room_id,
            )
            return

        cumulative_samples = start_packet_idx * 960
        logger.info(
            "Starting audio push for room %s: %d packets, starting at packet %d (%.1fms)",
            room_id,
            len(packets),
            start_packet_idx,
            start_ms,
        )

        for i in range(start_packet_idx, len(packets)):
            if cancel_event.is_set():
                logger.info("Audio push cancelled for room %s at packet %d", room_id, i)
                return

            frame = AudioFrame(
                sample_rate=48000,
                sample_num=cumulative_samples,
                channels=2,
                encoding=AudioEncoding.OPUS,
                data=packets[i],
                song_id=song_id_token,
            )

            await clients.broadcast(room_id, frame.bin)
            cumulative_samples += 960

            # Sleep at 1.5x speed interval
            await asyncio.sleep(PUSH_INTERVAL_SEC)

        logger.info(
            "Audio push completed for room %s: pushed %d packets",
            room_id,
            len(packets) - start_packet_idx,
        )


audio_push_manager = AudioPushManager()
