"""Utility to strip audio file metadata (tags, cover art, album info, etc.)."""

from __future__ import annotations

import os
import shutil
import subprocess

from utils import get_logger

logger = get_logger(__name__)


def strip_audio_metadata(file_path: str) -> bool:
    """Remove all metadata from an audio file in-place.

    Uses ffmpeg to strip every tag/container metadata (ID3, Vorbis comments,
    MP4 tags, cover art, …) while keeping the audio stream intact (no
    re-encoding).

    The command strips three layers of metadata:
    - ``-map_metadata:g -1`` — global container metadata
    - ``-map_metadata:s -1`` — per-stream metadata (e.g. Vorbis comments in OGG)
    - ``-map_chapters -1`` — chapter markers

    Args:
        file_path: Path to the audio file to strip.

    Returns:
        ``True`` if metadata was stripped successfully, ``False`` otherwise.
    """
    if not os.path.isfile(file_path):
        logger.error("strip_audio_metadata: file not found: %s", file_path)
        return False

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        logger.error("strip_audio_metadata: ffmpeg not found in PATH")
        return False

    tmp_path = file_path + ".strip_meta.tmp"
    try:
        cmd = [
            ffmpeg_bin,
            "-y",  # overwrite output
            "-i",
            file_path,
            "-map_metadata:g",
            "-1",  # strip global metadata
            "-map_metadata:s",
            "-1",  # strip per-stream metadata (Vorbis comments, etc.)
            "-map_chapters",
            "-1",  # strip chapter markers
            "-c",
            "copy",  # no re-encoding
            "-bitexact",  # avoid writing encoder tags / creation metadata
            tmp_path,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            logger.error(
                "strip_audio_metadata: ffmpeg failed for %s: %s",
                file_path,
                stderr[-500:],
            )
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return False

        os.replace(tmp_path, file_path)
        logger.info("Audio metadata stripped: %s", file_path)
        return True
    except subprocess.TimeoutExpired:
        logger.error("strip_audio_metadata: ffmpeg timed out for %s", file_path)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("strip_audio_metadata: unexpected error for %s", file_path)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False
