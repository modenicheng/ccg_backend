from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

from sqlalchemy import and_, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db import models
from db.session import AsyncSessionLocal
from mq.tasks import _download_and_cache_song_impl, _fetch_songlist_impl

import dotenv

dotenv.load_dotenv("../.env")

async def run(songlist_id: int = 9519555384) -> None:
    has_cookie = bool(os.getenv("CCG_QQ_MUSIC_COOKIE", "").strip())
    if not has_cookie:
        print("[WARN] CCG_QQ_MUSIC_COOKIE is empty; some songlists may return empty tracks")

    print(f"[STEP 1] Fetching and persisting songlist: {songlist_id}")
    result = await _fetch_songlist_impl(songlist_id)
    if not result:
        raise RuntimeError("fetch_songlist failed")

    db_songs, songlist = result
    if not db_songs:
        raise RuntimeError(
            "songlist persisted but has no songs. "
            "Please verify songlist visibility and CCG_QQ_MUSIC_COOKIE validity."
        )

    first_song = db_songs[0]
    if not first_song.platform_song_id:
        raise RuntimeError("first song has no platform_song_id")

    mid = first_song.platform_song_id
    print(f"[STEP 2] Download and cache first song: mid={mid}")
    cached_path = await _download_and_cache_song_impl(mid=mid)
    if not cached_path:
        raise RuntimeError("download_and_cache_song failed")

    print("[STEP 3] Verifying DB cached_path update")
    async with AsyncSessionLocal() as session:
        query = select(models.Song).where(
            and_(
                models.Song.platform == "qq",
                models.Song.platform_song_id == mid,
            )
        )
        song = (await session.execute(query)).scalars().first()

    if not song:
        raise RuntimeError("song not found in DB after cache flow")
    if song.cached_path != cached_path:
        raise RuntimeError(
            f"cached_path mismatch: db={song.cached_path}, returned={cached_path}"
        )

    file_path = Path(cached_path)
    if not file_path.exists():
        raise RuntimeError(f"cached file not found on disk: {cached_path}")

    print("✅ End-to-end flow completed")
    print(f"songlist_id={songlist_id}")
    print(f"songlist_db_id={songlist.id}")
    print(f"first_mid={mid}")
    print(f"cached_path={cached_path}")


if __name__ == "__main__":
    asyncio.run(run(9561851623))
