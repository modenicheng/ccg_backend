"""Tasks module for huey background jobs."""
from __future__ import annotations

# Standard library imports
import os
import asyncio
import logging
import threading
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Coroutine, TypeVar
from urllib.parse import urlparse

# Third-party imports
from huey import RedisHuey
import httpx
import qqmusic_api as qapi
from sqlalchemy.exc import InterfaceError as SQLAlchemyInterfaceError
from sqlalchemy.ext.asyncio import AsyncSession
from pydub import AudioSegment

# Local application imports
from config import app_config
from db.crud import (
    create_or_update_songlist,
    create_or_update_songs,
    update_song_cached_path,
    create_task_record,
)
from db.session import session_scope, engine
from utils import parse_cookie_string

logger = logging.getLogger("huey")


def _download_task_id(platform_song_id: str) -> str:
    """Build deterministic task id for song download status tracking."""
    return f"download_and_cache_song:{platform_song_id}"


_ASYNC_LOOP_LOCK = threading.Lock()
_ASYNC_LOOP: asyncio.AbstractEventLoop | None = None
_ASYNC_LOOP_THREAD: threading.Thread | None = None

REDIS_URI = app_config.redis_url

huey = RedisHuey("ccg-backend", url=REDIS_URI)

SONGLIST_FETCH_CONCURRENCY = app_config.songlist_fetch_concurrency
SONGLIST_FETCH_RETRIES = app_config.songlist_fetch_retries
SONGLIST_FETCH_BACKOFF_SECONDS = app_config.songlist_fetch_backoff_seconds
DOWNLOAD_RETRIES = app_config.audio_download_retries
DOWNLOAD_BACKOFF_SECONDS = app_config.audio_download_backoff_seconds
SONG_URL_RETRIES = app_config.song_url_retries
SONG_URL_BACKOFF_SECONDS = app_config.song_url_backoff_seconds
AUDIO_DOWNLOAD_DIR = app_config.audio_download_dir
QQ_MUSIC_COOKIE = app_config.qq_music_cookie
T = TypeVar("T")


def _to_jsonable(value: Any):  # pylint: disable=too-many-return-statements
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}

    if hasattr(value, "__table__"):
        table = getattr(value, "__table__", None)
        if table is not None and hasattr(table, "columns"):
            return {
                column.name: _to_jsonable(getattr(value, column.name, None))
                for column in table.columns
            }

    return str(value)


async def _persist_task_state(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    task_id: str,
    status: str,
    task_name: str,
    result: Any = None,
    error: str | None = None,
    session: AsyncSession | None = None,
) -> None:
    payload: dict[str, Any] = {}
    if result is not None:
        normalized = _to_jsonable(result)
        payload["result"] = normalized
    if error is not None:
        payload["error"] = error

    async def _write_task_state(db_session: AsyncSession) -> None:
        await create_task_record(
            session=db_session,
            task_id=task_id,
            task_name=task_name,
            status=status,
            result_json=payload or None,
        )

    if session is not None:
        try:
            await _write_task_state(session)
        except Exception as db_err:  # pylint: disable=broad-exception-caught
            logger.error(
                "Failed to persist task state for %s (%s): %s",
                task_id,
                task_name,
                db_err,
                exc_info=True,
            )
        return

    async with session_scope() as managed_session:
        try:
            await _write_task_state(managed_session)
        except Exception as db_err:  # pylint: disable=broad-exception-caught
            logger.error(
                "Failed to persist task state for %s (%s): %s",
                task_id,
                task_name,
                db_err,
                exc_info=True,
            )


def _ensure_background_event_loop() -> asyncio.AbstractEventLoop:
    global _ASYNC_LOOP, _ASYNC_LOOP_THREAD  # pylint: disable=global-statement
    with _ASYNC_LOOP_LOCK:
        if _ASYNC_LOOP is not None and not _ASYNC_LOOP.is_closed():
            return _ASYNC_LOOP

        loop = asyncio.new_event_loop()

        def _run_loop_forever() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(
            target=_run_loop_forever,
            name="huey-async-loop",
            daemon=True,
        )
        thread.start()

        _ASYNC_LOOP = loop
        _ASYNC_LOOP_THREAD = thread
        return loop


def _run_async(coro: Coroutine[Any, Any, T]) -> T:
    loop = _ensure_background_event_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


async def _with_retry(
    operation_name: str,
    task_factory: Callable[[], Awaitable[T]],
    retries: int,
    backoff_seconds: float,
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            return await task_factory()
        except Exception as err:  # pylint: disable=broad-exception-caught  # pylint: disable=broad-exception-caught
            last_error = err
            if attempt >= max(1, retries):
                break
            sleep_seconds = max(0.0, backoff_seconds) * (2**(attempt - 1))
            logger.warning("%s failed on attempt %s/%s, retrying in %.2fs: %s",
                           operation_name, attempt, max(1, retries), sleep_seconds, err)
            await asyncio.sleep(sleep_seconds)

    assert last_error is not None
    raise last_error


def _resolve_audio_download_path(mid: str,
                                 url: str,
                                 save_path: str | None = None) -> str:
    if save_path:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        return save_path

    parsed_path = urlparse(url).path
    ext = os.path.splitext(parsed_path)[1] or ".ogg"
    safe_mid = mid.strip() if isinstance(mid, str) and mid.strip() else "unknown"

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    configured_dir = AUDIO_DOWNLOAD_DIR.strip().lstrip("/\\")
    target_dir = (AUDIO_DOWNLOAD_DIR if os.path.isabs(AUDIO_DOWNLOAD_DIR) else
                  os.path.join(project_root, configured_dir))
    os.makedirs(target_dir, exist_ok=True)

    return os.path.join(target_dir, f"{safe_mid}{ext}")  # pylint: disable=line-too-long


if QQ_MUSIC_COOKIE:
    cookies = parse_cookie_string(QQ_MUSIC_COOKIE)
    if cookies:
        credential = qapi.Credential.from_cookies_dict(cookies)
        qapi.get_session().credential = credential
    else:
        logger.warning(
            "CCG_QQ_MUSIC_COOKIE is set but failed to parse; using default qqmusic_api session without credential"  # pylint: disable=line-too-long
        )
else:
    logger.warning(
        "CCG_QQ_MUSIC_COOKIE is not set; using default qqmusic_api session without credential"
    )


@huey.task()
def convert_to_opus(input_path, output_path=None, bitrate="128k"):
    """
    使用 pydub 转换音频到 Opus。
    """
    # 小丑了，qq 提供 opus 192k ，那我还转个 damn
    if not output_path:
        base, _ = os.path.splitext(input_path)
        output_path = base + ".opus"

    # 加载音频（pydub 自动根据扩展名选择格式）
    audio: AudioSegment = AudioSegment.from_file(input_path)

    # 导出为 Opus
    audio.export(output_path, format="opus", bitrate=bitrate)
    logger.info("Audio converted to opus: %s", output_path)
    return output_path


@huey.task()
def download_audio_file(url, save_path=None, mid: str | None = None):
    """
    Download audio file from URL and save to specified path.

    Args:
        url: Audio file URL
        save_path: Optional local path to save the file
        mid: Optional song MID identifier

    Returns:
        str or None: Path to downloaded file or None on failure
    """
    return _run_async(_download_audio_file_impl(url=url, save_path=save_path, mid=mid))


async def _download_audio_file_impl(url, save_path=None, mid: str | None = None):
    """
    下载音频文件并保存到指定路径。
    """
    try:
        target_mid = (mid or
                      os.path.splitext(os.path.basename(urlparse(url).path))[0] or
                      "unknown")
        final_save_path = _resolve_audio_download_path(target_mid, url, save_path)

        async with httpx.AsyncClient() as http_client:
            response = await _with_retry(
                operation_name=f"download audio from {url}",
                task_factory=lambda: http_client.get(url),
                retries=DOWNLOAD_RETRIES,
                backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
            )
        response.raise_for_status()
        with open(final_save_path, "wb") as f:
            f.write(response.content)
        logger.info("Audio downloaded successfully: %s", final_save_path)
        return final_save_path
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error when downloading audio from %s: %s", url, e, exc_info=True)
        return None


async def _get_song_url(
        mid: str,
        filetype: qapi.song.SongFileType = qapi.song.SongFileType.OGG_320
) -> str | None:
    try:

        async def _fetch_song_urls() -> dict[str, Any]:
            return await qapi.song.get_song_urls(
                [mid],
                file_type=filetype,
            )

        result = await _with_retry(
            operation_name=f"fetch song url for {mid}",
            task_factory=_fetch_song_urls,
            retries=SONG_URL_RETRIES,
            backoff_seconds=SONG_URL_BACKOFF_SECONDS,
        )
        url = result.get(mid)
        if not url:
            logger.error("No URL found for song mid %s", mid)
        return url
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error fetching song URL for mid %s: %s", mid, e, exc_info=True)
        return None


@huey.task()
def download_and_cache_song(mid: str, save_path: str | None = None):
    """A function that wrap the inner func. Run this func will push the task to huey.
    Run the inner func will directly execute the logic, which is more suitable for testing and debugging.  # pylint: disable=line-too-long

    Args:
        mid (str): _description_
        save_path (str | None, optional): _description_. Defaults to None.

    Returns:
        _type_: _description_
    """
    return _run_async(_download_and_cache_song_impl(mid=mid, save_path=save_path))


async def _download_and_cache_song_impl(mid: str, save_path: str | None = None):
    """下载单曲并将本地缓存路径写回数据库。"""
    if not mid:
        logger.error("download_and_cache_song got empty mid")
        return None

    task_id = _download_task_id(mid)
    await _persist_task_state(
        task_id=task_id,
        task_name="download_and_cache_song",
        status="running",
        result={
            "platform": "qq",
            "platform_song_id": mid
        },
    )

    song_url = await _get_song_url(mid, filetype=qapi.song.SongFileType.OGG_320)
    if not song_url:
        await _persist_task_state(
            task_id=task_id,
            task_name="download_and_cache_song",
            status="failed",
            error="Failed to resolve song url",
        )
        return None

    downloaded_path = await _download_audio_file_impl(song_url,
                                                      save_path=save_path,
                                                      mid=mid)
    if not downloaded_path:
        await _persist_task_state(
            task_id=task_id,
            task_name="download_and_cache_song",
            status="failed",
            error="Failed to download audio file",
        )
        return None

    try:
        async with session_scope() as session:
            song = await update_song_cached_path(
                session=session,
                platform="qq",
                platform_song_id=mid,
                cached_path=downloaded_path,
            )
            if not song:
                logger.warning(
                    "Audio downloaded for %s but song not found in DB, skipped cached_path update: %s",
                    mid,
                    downloaded_path  # pylint: disable=line-too-long
                )
            else:
                logger.info("Updated cached_path for %s: %s", mid, downloaded_path)

            await _persist_task_state(
                task_id=task_id,
                task_name="download_and_cache_song",
                status="success",
                result={
                    "platform": "qq",
                    "platform_song_id": mid,
                    "cached_path": downloaded_path,
                },
                session=session,
            )
            return downloaded_path
    except Exception as err:  # pylint: disable=broad-exception-caught
        logger.error("Failed to update cached_path for %s: %s", mid, err, exc_info=True)
        await _persist_task_state(
            task_id=task_id,
            task_name="download_and_cache_song",
            status="failed",
            error=f"Failed to update song cached_path: {err}",
        )
        return None


@huey.task()
def fetch_songlist(songlist_id: int,
                   cookie_str: str | None = None,
                   task_id: str | None = None):
    """
    Fetch songlist from QQ Music and persist to database.

    Args:
        songlist_id: QQ Music songlist ID
        cookie_str: Optional cookie string for authentication
        task_id: Optional task ID for state tracking

    Returns:
        dict or None: Songlist data or None on failure
    """
    if task_id:
        _run_async(
            _persist_task_state(task_id=task_id,
                                task_name="fetch_songlist",
                                status="running"))

    try:
        result = _run_async(
            _fetch_songlist_impl(songlist_id=songlist_id, cookie_str=cookie_str))

        if task_id:
            if result is None:
                _run_async(
                    _persist_task_state(
                        task_id=task_id,
                        task_name="fetch_songlist",
                        status="failed",
                        error="fetch_songlist returned no result",
                    ))
            else:
                _run_async(
                    _persist_task_state(
                        task_id=task_id,
                        task_name="fetch_songlist",
                        status="success",
                        result=result,
                    ))
        return result
    except Exception as err:
        if task_id:
            _run_async(
                _persist_task_state(
                    task_id=task_id,
                    task_name="fetch_songlist",
                    status="failed",
                    error=str(err),
                ))
        raise


async def _fetch_songlist_impl(songlist_id: int, cookie_str: str | None = None):  # pylint: disable=too-many-locals
    """
    使用 qqmusic_api 获取歌单信息。
    {
        "dirinfo": {
            "id": 9561851623,
            "host_uin": 939861972,
            "dirid": 8,
            "title": "崩铁角色+地区",
            "picurl": "https://music-file.y.qq.com/songlist/user/NKoqNeC5NKSA/68a3ff5b/HjXL0fy6EiixGOe8lSMEtc_190f80.jpg?imageView2/4/w/600/h/600",  # pylint: disable=line-too-long
            "picid": 0,
            "desc": "",
            "vec_tagid": [
                6,
                24,
                39
            ],
            "vec_tagname": [],
            "ctime": 1755577967,
            "mtime": 1769833631,
            "listennum": 0,
            "ordernum": 0,
            "picmid": "",
            "dirtype": 0,
            "host_nick": "程家麒",
            "songnum": 460,
            "ordertime": 0,
            "show": 1,
            "picurl2": "",
            "song_update_time": 0,
            "song_update_num": 0,
            "disstype": 0,
            "ai_uin": 939861972,
            "dv2": 0,
            "dir_show": 1,
            "encrypt_uin": "NKoqNeC5NKSA",
            "encrypt_ai_uin": "",
            "owndir": 0,
            "headurl": "http://y.gtimg.cn/music/photo_new/T001R500x500M000001SFrQc43SA1o_2.jpg",
            "tag": [...],
            "creator": {
                ...
            },
            "status": 0,
            "edge_mark": "",
            "layer_url": "",
            "ext1": "",
            "ext2": "",
            "origin_title": "",
            "ad_tag": false,
            "aiToast": "",
            "role": 1,
            "rl2": 0
        },
        "total_song_num": 460,
        "songlist_size": 10,
        "songlist": [...],
        "songtag": [],
        "orderlist": []
    }
    """
    if cookie_str:
        credential = qapi.Credential.from_cookies_dict(parse_cookie_string(cookie_str))  # pylint: disable=redefined-outer-name
        qapi.get_session().credential = credential
        logger.info(
            "Using custom credential from provided cookie string for fetching songlist %s",
            songlist_id  # pylint: disable=line-too-long
        )

    try:
        first_songlist = await _with_retry(
            operation_name=f"fetch songlist detail {songlist_id}",
            task_factory=lambda: qapi.songlist.get_detail(songlist_id),
            retries=SONGLIST_FETCH_RETRIES,
            backoff_seconds=SONGLIST_FETCH_BACKOFF_SECONDS,
        )
        dirinfo = first_songlist["dirinfo"]
        total: int = first_songlist["total_song_num"]
        title: str = dirinfo["title"]
        songs: list = first_songlist["songlist"]

        semaphore = asyncio.Semaphore(max(1, SONGLIST_FETCH_CONCURRENCY))

        async def fetch_page(page: int) -> list:
            async with semaphore:
                for attempt in range(1, SONGLIST_FETCH_RETRIES + 1):
                    try:
                        songlist_page = await qapi.songlist.get_detail(songlist_id,
                                                                       page=page)
                        return songlist_page["songlist"]
                    except Exception as page_err:  # pylint: disable=broad-exception-caught
                        if attempt >= SONGLIST_FETCH_RETRIES:
                            logger.error(
                                "Failed to fetch page %s of songlist %s after %s attempts: %s",
                                page,
                                songlist_id,
                                attempt,
                                page_err  # pylint: disable=line-too-long
                            )
                            raise
                        sleep_seconds = SONGLIST_FETCH_BACKOFF_SECONDS * (2**(attempt -
                                                                              1))
                        logger.warning(
                            "Fetch page %s failed on attempt %s/%s, retrying in %.2fs: %s",
                            page,
                            attempt,
                            SONGLIST_FETCH_RETRIES,
                            sleep_seconds,
                            page_err  # pylint: disable=line-too-long
                        )
                        await asyncio.sleep(sleep_seconds)

            return []

        total_pages = (total + 9) // 10
        pages = range(2, total_pages + 1)
        results = await asyncio.gather(*[fetch_page(page) for page in pages])
        for result in results:
            songs.extend(result)

        db_retries = max(2, SONG_URL_RETRIES)
        for attempt in range(1, db_retries + 1):
            try:
                async with session_scope() as session:
                    songlist = await create_or_update_songlist(
                        session=session,
                        platform="qq",
                        platform_songlist_id=songlist_id,
                        title=title,
                        cover_url=dirinfo.get("picurl"),
                        metadata_json=first_songlist,
                        creator_name=dirinfo.get("host_nick"),
                    )

                    db_songs = await create_or_update_songs(
                        session=session,
                        songlist_id=songlist.id,
                        songs=songs,
                    )

                    logger.info(
                        "Fetched songlist %s: %s with %s songs, persisted %s songs",
                        songlist_id,
                        title,
                        total,
                        len(db_songs)  # pylint: disable=line-too-long
                    )
                    return {
                        "songlist": songlist,
                        "songs": db_songs,
                    }
            except SQLAlchemyInterfaceError as db_err:
                err_text = str(db_err).lower()
                if "another operation is in progress" not in err_text:
                    raise

                if attempt >= db_retries:
                    raise

                logger.warning(
                    "DB operation hit asyncpg busy-connection error on attempt %s/%s, disposing engine and retrying: %s",
                    attempt,
                    db_retries,
                    db_err  # pylint: disable=line-too-long
                )
                await engine.dispose()
                sleep_seconds = max(0.1, SONG_URL_BACKOFF_SECONDS) * (2**(attempt - 1))
                await asyncio.sleep(sleep_seconds)

        return None
    except KeyError as e:
        logger.error("KeyError fetching songlist: %s", e, exc_info=True)
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error fetching songlist: %s", e, exc_info=True)
        return None
