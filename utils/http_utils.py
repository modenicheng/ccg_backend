"""
HTTP实用工具函数
提供通用的HTTP相关功能，如Range请求处理等
"""
from __future__ import annotations

from typing import Optional
from fastapi import HTTPException, Response


def build_range_response(content: bytes, media_type: str,
                         range_header: Optional[str]) -> Response:
    """
    构建支持Range请求的响应

    Args:
        content: 字节内容
        media_type: 媒体类型，如'audio/mpeg'
        range_header: Range请求头值，如'bytes=0-999'

    Returns:
        Response: FastAPI Response对象

    Raises:
        HTTPException: 当Range头无效时抛出416状态码异常
    """
    total = len(content)
    common_headers = {
        "Accept-Ranges": "bytes",
    }

    if not range_header:
        return Response(
            content=content,
            media_type=media_type,
            headers={
                **common_headers, "Content-Length": str(total)
            },
        )

    if not range_header.startswith("bytes="):
        raise HTTPException(status_code=416, detail="Invalid Range header")

    range_spec = range_header.replace("bytes=", "", 1).strip()
    if "," in range_spec:
        raise HTTPException(status_code=416, detail="Multiple ranges are not supported")

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
        raise HTTPException(status_code=416, detail="Invalid Range header") from e

    if total == 0 or start < 0 or end < start or start >= total:
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{total}",
                **common_headers
            },
        )

    end = min(end, total - 1)
    partial = content[start:end + 1]
    return Response(
        content=partial,
        status_code=206,
        media_type=media_type,
        headers={
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Content-Length": str(len(partial)),
        },
    )
