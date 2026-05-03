"""Utility to strip audio file metadata (tags, cover art, album info, etc.)."""

from __future__ import annotations

import os
import shutil
import subprocess

from utils import get_logger

logger = get_logger(__name__)

# OGG Vorbis 是 QQ 音乐返回的主要格式，其元数据（Vorbis comments）内嵌在
# 编码码流中，使用 ``-c copy`` 无法清除。必须重新编码才能生成干净码流。
_VORBIS_REENCODE_BITRATE = "320k"
VORBIS_CODEC_NAMES = frozenset({"vorbis"})


def _probe_audio_codec(ffprobe_bin: str, file_path: str) -> str | None:
    """Probe the primary audio codec name of *file_path* using ffprobe.

    Returns the codec name string (e.g. ``"vorbis"``, ``"mp3"``, ``"aac"``)
    or ``None`` if detection fails.
    """
    try:
        result = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "quiet",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return None


def strip_audio_metadata(file_path: str) -> bool:
    """Remove all metadata from an audio file in-place.

    Uses ffmpeg to strip every tag/container metadata (ID3, Vorbis comments,
    MP4 tags, cover art, …).

    For **Vorbis** codec (the dominant format served by QQ Music) metadata is
    embedded inside the codec bitstream itself and **cannot** be removed with
    ``-c copy`` — the audio stream is re-encoded to Vorbis at 320 kbit/s to
    produce clean packets free of any comments.

    For all other codecs the function uses stream-copy and only rewrites the
    container, which is fast and lossless.

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

    ffprobe_bin = shutil.which("ffprobe")

    # 探测编码类型，决定使用 copy 还是重新编码
    codec = None
    needs_reencode = False
    if ffprobe_bin:
        codec = _probe_audio_codec(ffprobe_bin, file_path)
        needs_reencode = codec is not None and codec.lower() in VORBIS_CODEC_NAMES
        if needs_reencode:
            logger.info(
                "strip_audio_metadata: detected Vorbis codec, will re-encode at %s",
                _VORBIS_REENCODE_BITRATE,
            )
        elif codec:
            logger.debug("strip_audio_metadata: codec=%s, using stream-copy", codec)
    else:
        logger.warning(
            "strip_audio_metadata: ffprobe not available; falling back to stream-copy")

    tmp_path = file_path + ".strip_meta.tmp"
    try:
        if needs_reencode:
            # ── Vorbis：重新编码以彻底清除内嵌 Vorbis comments ──────────
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                file_path,
                "-c:a",
                "libvorbis",
                "-b:a",
                _VORBIS_REENCODE_BITRATE,
                "-map_metadata",
                "-1",  # 禁止写入容器元数据
                "-map_chapters",
                "-1",
                "-bitexact",  # 禁止写入编码器标签
                tmp_path,
            ]
        else:
            # ── 其他格式：流复制，速度最快且无损 ─────────────────────────
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                file_path,
                "-map_metadata:g",
                "-1",  # 全局容器元数据
                "-map_metadata:s",
                "-1",  # 流级容器元数据
                "-map_chapters",
                "-1",  # 章节标记
                "-c",
                "copy",  # 流复制（无重新编码）
                "-bitexact",
                tmp_path,
            ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120 if needs_reencode else 60,
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
