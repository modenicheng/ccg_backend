"""File-based caching for song assets."""

from __future__ import annotations

import logging
import mimetypes
import os
from collections import OrderedDict

from anyio import open_file
from fastapi import HTTPException

from config import app_config

logger = logging.getLogger(__name__)

ASSET_CACHE_MAX_ITEMS = app_config.asset_cache_max_items
ASSET_CACHE_MAX_BYTES = 256 * 1024 * 1024  # 256 MB

_song_asset_cache: OrderedDict[str, tuple[float, int, bytes, str]] = OrderedDict()
_cache_total_bytes: int = 0


def _evict_excess() -> None:
    """Evict LRU entries while either count or byte limit is exceeded."""
    global _cache_total_bytes
    while len(_song_asset_cache) > 0 and (len(_song_asset_cache) > ASSET_CACHE_MAX_ITEMS
                                          or
                                          _cache_total_bytes > ASSET_CACHE_MAX_BYTES):
        _, (_, _, content, _) = _song_asset_cache.popitem(last=False)
        _cache_total_bytes -= len(content)
        logger.warning(
            "Asset cache eviction: items=%d, total_bytes=%d",
            len(_song_asset_cache),
            _cache_total_bytes,
        )


async def load_song_asset_with_cache(path: str) -> tuple[bytes, str]:
    """
    使用内存 LRU 缓存音频文件内容，减少重复磁盘 IO。

    缓存键使用文件绝对路径，失效判断基于 mtime + size。
    """
    global _cache_total_bytes

    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="Cached audio file not found")

    stat = os.stat(abs_path)
    mtime = stat.st_mtime
    size = stat.st_size

    cached = _song_asset_cache.get(abs_path)
    if cached and cached[0] == mtime and cached[1] == size:
        _song_asset_cache.move_to_end(abs_path)
        return cached[2], cached[3]

    # If replacing an existing entry, subtract its old size first
    old = _song_asset_cache.get(abs_path)
    if old is not None:
        _cache_total_bytes -= len(old[2])

    async with await open_file(abs_path, mode="rb") as f:
        content = await f.read()

    media_type = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
    _song_asset_cache[abs_path] = (mtime, size, content, media_type)
    _song_asset_cache.move_to_end(abs_path)
    _cache_total_bytes += len(content)

    _evict_excess()

    return content, media_type
