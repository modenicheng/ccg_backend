from __future__ import annotations
import mimetypes
import os
from collections import OrderedDict

from anyio import open_file
from fastapi import HTTPException

ASSET_CACHE_MAX_ITEMS = int(os.getenv("CCG_ASSET_CACHE_MAX_ITEMS", "64"))
_song_asset_cache: OrderedDict[str, tuple[float, int, bytes,
                                          str]] = OrderedDict()


async def load_song_asset_with_cache(path: str) -> tuple[bytes, str]:
    """
    使用内存 LRU 缓存音频文件内容，减少重复磁盘 IO。

    缓存键使用文件绝对路径，失效判断基于 mtime + size。
    """
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404,
                            detail="Cached audio file not found")

    stat = os.stat(abs_path)
    mtime = stat.st_mtime
    size = stat.st_size

    cached = _song_asset_cache.get(abs_path)
    if cached and cached[0] == mtime and cached[1] == size:
        _song_asset_cache.move_to_end(abs_path)
        return cached[2], cached[3]

    async with await open_file(abs_path, mode="rb") as f:
        content = await f.read()

    media_type = mimetypes.guess_type(
        abs_path)[0] or "application/octet-stream"
    _song_asset_cache[abs_path] = (mtime, size, content, media_type)
    _song_asset_cache.move_to_end(abs_path)

    while len(_song_asset_cache) > ASSET_CACHE_MAX_ITEMS:
        _song_asset_cache.popitem(last=False)

    return content, media_type
