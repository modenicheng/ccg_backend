"""Utility to strip audio file metadata (tags, cover art, album info, etc.)."""

from __future__ import annotations

import os
import shutil
import subprocess

from utils import get_logger

logger = get_logger(__name__)


def strip_audio_metadata(file_path: str) -> bool:
    """Remove all metadata from an audio file in-place.

    Uses ffmpeg ``-map_metadata -1 -c copy`` to strip every tag/container
    metadata (ID3, Vorbis comments, MP4 tags, cover art, …) while keeping
    the audio stream intact (no re-encoding).

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
            "-map_metadata",
            "-1",  # strip all metadata
            "-c",
            "copy",  # no re-encoding
            "-fflags",
            "+bitexact",  # avoid writing encoder tag
            "-flags:v",
            "+bitexact",  # avoid writing encoder tag for video
            "-flags:a",
            "+bitexact",  # avoid writing encoder tag for audio
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


def convert_and_strip_metadata(
    input_bytes: bytes,
    output_path: str,
    input_format: str = "ogg",
    bitrate: str = "128k",
    timeout: int = 120,
) -> bool:
    """Convert audio to Opus and strip all metadata in a single ffmpeg pass.

    Pipes *input_bytes* into ffmpeg's stdin, re-encodes to libopus with VBR,
    removes every tag/container metadata, and writes the result atomically.

    Args:
        input_bytes: Raw audio bytes (e.g. from an HTTP response).
        output_path: Destination file path (overwritten atomically).
        input_format: Container format hint for ffmpeg (default ``"ogg"``).
        bitrate: Target bitrate (default ``"128k"``).
        timeout: Subprocess timeout in seconds.

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        logger.error("convert_and_strip_metadata: ffmpeg not found in PATH")
        return False

    tmp_path = output_path + ".convert.tmp"
    try:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            input_format,
            "-i",
            "pipe:0",
            "-c:a",
            "libopus",
            "-b:a",
            bitrate,
            "-vbr",
            "on",
            "-map_metadata",
            "-1",
            "-fflags",
            "+bitexact",
            "-flags:a",
            "+bitexact",
            "-f",
            "ogg",
            tmp_path,
        ]
        result = subprocess.run(
            cmd,
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            logger.error(
                "convert_and_strip_metadata: ffmpeg failed for %s: %s",
                output_path,
                stderr[-500:],
            )
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return False

        os.replace(tmp_path, output_path)
        logger.info("Audio converted to opus and metadata stripped: %s", output_path)
        return True
    except subprocess.TimeoutExpired:
        logger.error("convert_and_strip_metadata: ffmpeg timed out for %s", output_path)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("convert_and_strip_metadata: unexpected error for %s",
                         output_path)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False


def ensure_opus_encoded(file_path: str, bitrate: str = "128k") -> bool:
    """Ensure an audio file is Opus-encoded, converting it in-place if not.

    Uses ffprobe to check the codec.  If the file is already Opus, returns
    immediately.  Otherwise reads the file, converts via
    :func:`convert_and_strip_metadata`, and replaces the original.

    Args:
        file_path: Path to the audio file.
        bitrate: Target bitrate for conversion (default ``"128k"``).

    Returns:
        ``True`` if the file is (now) Opus-encoded, ``False`` on failure.
    """
    if not os.path.isfile(file_path):
        logger.error("ensure_opus_encoded: file not found: %s", file_path)
        return False

    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        logger.error("ensure_opus_encoded: ffprobe not found in PATH")
        return False

    try:
        probe_result = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                file_path,
            ],
            capture_output=True,
            timeout=10,
        )
        codec = probe_result.stdout.decode().strip()
        if codec == "opus":
            return True

        logger.info("ensure_opus_encoded: %s is %s, converting to opus", file_path,
                    codec)
        with open(file_path, "rb") as f:
            input_bytes = f.read()
        return convert_and_strip_metadata(input_bytes, file_path, bitrate=bitrate)
    except subprocess.TimeoutExpired:
        logger.error("ensure_opus_encoded: ffprobe timed out for %s", file_path)
        return False
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("ensure_opus_encoded: unexpected error for %s", file_path)
        return False
