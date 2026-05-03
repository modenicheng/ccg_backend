"""Audio file response utilities (streaming from disk, no in-memory cache)."""

from __future__ import annotations

import mimetypes
import os

from fastapi import HTTPException
from fastapi.responses import FileResponse

from utils import get_logger

logger = get_logger(__name__)


def build_file_response(path: str,
                        content_disposition: str | None = None) -> FileResponse:
    """Build a FileResponse that streams from disk with Range support.

    Args:
        path: Absolute path to the audio file.
        content_disposition: Optional Content-Disposition header value.

    Returns:
        FileResponse streaming the file from disk.

    Raises:
        HTTPException: If the file does not exist.
    """
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="Audio file not found")

    media_type = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
    headers: dict[str, str] = {"Accept-Ranges": "bytes"}
    if content_disposition:
        headers["Content-Disposition"] = content_disposition

    return FileResponse(abs_path, media_type=media_type, headers=headers)
