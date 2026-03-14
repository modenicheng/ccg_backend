"""
Songlist to cache flow example.

This module demonstrates the complete workflow of fetching a QQ Music songlist,
persisting it to the database, downloading the first song, and caching it locally.

Example usage:
    python songlist_to_cache_flow.py --songlist-id 9561851623
"""

# pylint: disable=unexpected-line-ending-format

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Tuple

import dotenv
# pylint: disable=import-error
from sqlalchemy import and_, select
# pylint: enable=import-error

# Add project root to sys.path before importing local modules
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# pylint: disable=import-error,wrong-import-position
from db import models
from db.session import session_scope
from mq.tasks import _download_and_cache_song_impl, _fetch_songlist_impl
# pylint: enable=import-error,wrong-import-position

dotenv.load_dotenv("../.env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class SonglistToCacheError(Exception):
    """Custom exception for songlist to cache flow errors."""


async def run_songlist_to_cache_flow(songlist_id: int) -> Tuple[int, str, str]:
    """
    Execute the full songlist to cache flow.

    Args:
        songlist_id: QQ Music songlist ID

    Returns:
        Tuple containing (songlist_db_id, first_song_mid, cached_path)

    Raises:
        SonglistToCacheError: If any step in the flow fails
    """
    # Check for required environment variables
    has_cookie = bool(os.getenv("CCG_QQ_MUSIC_COOKIE", "").strip())
    if not has_cookie:
        logger.warning(
            "CCG_QQ_MUSIC_COOKIE is empty; some songlists may return empty tracks")

    logger.info("Step 1: Fetching and persisting songlist: %d", songlist_id)
    result = await _fetch_songlist_impl(songlist_id)
    if not result:
        raise SonglistToCacheError("fetch_songlist failed")

    db_songs, songlist = result
    if not db_songs:
        raise SonglistToCacheError(
            "Songlist persisted but has no songs. "
            "Please verify songlist visibility and CCG_QQ_MUSIC_COOKIE validity.")

    first_song = db_songs[0]
    if not first_song.platform_song_id:
        raise SonglistToCacheError("First song has no platform_song_id")

    mid = first_song.platform_song_id
    logger.info("Step 2: Download and cache first song: mid=%s", mid)
    cached_path = await _download_and_cache_song_impl(mid=mid)
    if not cached_path:
        raise SonglistToCacheError("download_and_cache_song failed")

    logger.info("Step 3: Verifying DB cached_path update")
    async with session_scope() as session:
        query = select(models.Song).where(
            and_(
                models.Song.platform == "qq",
                models.Song.platform_song_id == mid,
            ))
        song = (await session.execute(query)).scalars().first()

    if not song:
        raise SonglistToCacheError("Song not found in DB after cache flow")
    if song.cached_path != cached_path:
        raise SonglistToCacheError(
            f"Cached_path mismatch: db={song.cached_path}, returned={cached_path}")

    file_path = Path(cached_path)
    if not file_path.exists():
        raise SonglistToCacheError(f"Cached file not found on disk: {cached_path}")

    logger.info("✅ End-to-end flow completed")
    logger.info("Songlist ID: %d", songlist_id)
    logger.info("Songlist DB ID: %d", songlist.id)
    logger.info("First song MID: %s", mid)
    logger.info("Cached path: %s", cached_path)

    return songlist.id, mid, cached_path


async def run(songlist_id: int = 9519555384) -> None:
    """
    Legacy function for backward compatibility.

    Args:
        songlist_id: QQ Music songlist ID
    """
    try:
        songlist_db_id, first_mid, cached_path = await run_songlist_to_cache_flow(
            songlist_id)
        print("✅ End-to-end flow completed")
        print(f"songlist_id={songlist_id}")
        print(f"songlist_db_id={songlist_db_id}")
        print(f"first_mid={first_mid}")
        print(f"cached_path={cached_path}")
    except SonglistToCacheError as e:
        logger.error("Flow failed: %s", e)
        raise RuntimeError(f"Flow failed: {e}") from e


def main() -> None:
    """
    Main entry point with command line argument support.
    """
    parser = argparse.ArgumentParser(
        description="Execute songlist to cache flow for QQ Music songlists.")
    parser.add_argument(
        "--songlist-id",
        "-s",
        type=int,
        default=9561851623,
        help="QQ Music songlist ID (default: 9561851623)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")

    logger.info("Starting songlist to cache flow for songlist ID: %d", args.songlist_id)

    try:
        asyncio.run(run(args.songlist_id))
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(130)
    except Exception as e:  # pylint: disable=broad-except
        logger.error("Unhandled exception: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
