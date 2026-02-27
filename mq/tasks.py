from typing import Any, Awaitable, Callable, TypeVar
from urllib.parse import urlparse

from huey import RedisHuey
import os
import httpx

import qqmusic_api as qapi

from db.crud import create_or_update_songlist, create_or_update_songs, update_song_cached_path
from db.session import AsyncSessionLocal
from sqlalchemy.ext.asyncio import AsyncSession
import utils
from dotenv import load_dotenv
import asyncio

load_dotenv()
# from db.session import AsyncSessionLocal
# from db.models import Song

logger = utils.logger.get_logger(__name__)
http_client = httpx.AsyncClient()

REDIS_URI = os.getenv("CCG_REDIS_URL", "localhost")
huey = RedisHuey('ccg-backend', host=REDIS_URI)

from pydub import AudioSegment
import asyncio

SONGLIST_FETCH_CONCURRENCY = int(
    os.getenv("CCG_SONGLIST_FETCH_CONCURRENCY", "8"))
SONGLIST_FETCH_RETRIES = int(os.getenv("CCG_SONGLIST_FETCH_RETRIES", "5"))
SONGLIST_FETCH_BACKOFF_SECONDS = float(
    os.getenv("CCG_SONGLIST_FETCH_BACKOFF_SECONDS", "0.4"))
DOWNLOAD_RETRIES = int(os.getenv("CCG_AUDIO_DOWNLOAD_RETRIES", "3"))
DOWNLOAD_BACKOFF_SECONDS = float(
    os.getenv("CCG_AUDIO_DOWNLOAD_BACKOFF_SECONDS", "0.4"))
SONG_URL_RETRIES = int(os.getenv("CCG_SONG_URL_RETRIES", "3"))
SONG_URL_BACKOFF_SECONDS = float(
    os.getenv("CCG_SONG_URL_BACKOFF_SECONDS", "0.4"))
AUDIO_DOWNLOAD_DIR = os.getenv("CCG_AUDIO_DOWNLOAD_DIR",
                               "assets/audio").strip() or "assets/audio"
QQ_MUSIC_COOKIE = os.getenv("CCG_QQ_MUSIC_COOKIE", "").strip()
T = TypeVar("T")


async def _with_retry(operation_name: str,
                      task_factory: Callable[[], Awaitable[T]], retries: int,
                      backoff_seconds: float) -> T:
    last_error: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            return await task_factory()
        except Exception as err:
            last_error = err
            if attempt >= max(1, retries):
                break
            sleep_seconds = max(0.0, backoff_seconds) * (2**(attempt - 1))
            logger.warning(
                f"{operation_name} failed on attempt {attempt}/{max(1, retries)}, retrying in {sleep_seconds:.2f}s: {err}"
            )
            await asyncio.sleep(sleep_seconds)

    assert last_error is not None
    raise last_error


def _parse_cookie_string(cookie_str: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in cookie_str.split(";"):
        part = item.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        cookies[key] = value.strip()
    return cookies


def _resolve_audio_download_path(mid: str,
                                 url: str,
                                 save_path: str | None = None) -> str:
    if save_path:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        return save_path

    parsed_path = urlparse(url).path
    ext = os.path.splitext(parsed_path)[1] or ".ogg"
    safe_mid = mid.strip() if isinstance(mid,
                                         str) and mid.strip() else "unknown"

    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), ".."))
    configured_dir = AUDIO_DOWNLOAD_DIR.strip().lstrip("/\\")
    target_dir = AUDIO_DOWNLOAD_DIR if os.path.isabs(
        AUDIO_DOWNLOAD_DIR) else os.path.join(project_root, configured_dir)
    os.makedirs(target_dir, exist_ok=True)

    return os.path.join(target_dir, f"{safe_mid}{ext}")


if QQ_MUSIC_COOKIE:
    cookies = _parse_cookie_string(QQ_MUSIC_COOKIE)
    if cookies:
        credential = qapi.Credential.from_cookies_dict(cookies)
        qapi.get_session().credential = credential
    else:
        logger.warning(
            "CCG_QQ_MUSIC_COOKIE is set but failed to parse; using default qqmusic_api session without credential"
        )
else:
    logger.warning(
        "CCG_QQ_MUSIC_COOKIE is not set; using default qqmusic_api session without credential"
    )


@huey.task()
def convert_to_opus(input_path, output_path=None, bitrate='128k'):
    """
    使用 pydub 转换音频到 Opus。
    """
    # 小丑了，qq 提供 opus 192k ，那我还转个 damn
    if not output_path:
        base, _ = os.path.splitext(input_path)
        output_path = base + '.opus'

    # 加载音频（pydub 自动根据扩展名选择格式）
    audio: AudioSegment = AudioSegment.from_file(input_path)

    # 导出为 Opus
    audio.export(output_path, format='opus', bitrate=bitrate)
    logger.info(f"Audio converted to opus: {output_path}")
    return output_path


@huey.task()
async def download_audio_file(url, save_path=None, mid: str | None = None):
    return await _download_audio_file_impl(url=url,
                                           save_path=save_path,
                                           mid=mid)


async def _download_audio_file_impl(url,
                                    save_path=None,
                                    mid: str | None = None):
    """
    下载音频文件并保存到指定路径。
    """
    try:
        target_mid = mid or os.path.splitext(
            os.path.basename(urlparse(url).path))[0] or "unknown"
        final_save_path = _resolve_audio_download_path(target_mid, url,
                                                       save_path)

        response = await _with_retry(
            operation_name=f"download audio from {url}",
            task_factory=lambda: http_client.get(url),
            retries=DOWNLOAD_RETRIES,
            backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        )
        response.raise_for_status()
        with open(final_save_path, 'wb') as f:
            f.write(response.content)
        logger.info(f"Audio downloaded successfully: {final_save_path}")
        return final_save_path
    except Exception as e:
        logger.error(f"Error when downloading audio from {url}: {e}",
                     exc_info=True)
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
            logger.error(f"No URL found for song mid {mid}")
        return url
    except Exception as e:
        logger.error(f"Error fetching song URL for mid {mid}: {e}",
                     exc_info=True)
        return None


@huey.task()
async def download_and_cache_song(mid: str, save_path: str | None = None):
    """A function that wrap the inner func. Run this func will push the task to huey.
    Run the inner func will directly execute the logic, which is more suitable for testing and debugging.

    Args:
        mid (str): _description_
        save_path (str | None, optional): _description_. Defaults to None.

    Returns:
        _type_: _description_
    """
    return await _download_and_cache_song_impl(mid=mid, save_path=save_path)


async def _download_and_cache_song_impl(mid: str,
                                        save_path: str | None = None):
    """下载单曲并将本地缓存路径写回数据库。"""
    if not mid:
        logger.error("download_and_cache_song got empty mid")
        return None

    song_url = await _get_song_url(mid,
                                   filetype=qapi.song.SongFileType.OGG_320)
    if not song_url:
        return None

    downloaded_path = await _download_audio_file_impl(song_url,
                                                      save_path=save_path,
                                                      mid=mid)
    if not downloaded_path:
        return None

    async with AsyncSessionLocal() as session:
        try:
            song = await update_song_cached_path(
                session=session,
                platform="qq",
                platform_song_id=mid,
                cached_path=downloaded_path,
            )
            await session.commit()
            if not song:
                logger.warning(
                    f"Audio downloaded for {mid} but song not found in DB, skipped cached_path update: {downloaded_path}"
                )
            else:
                logger.info(
                    f"Updated cached_path for {mid}: {downloaded_path}")
            return downloaded_path
        except Exception as err:
            await session.rollback()
            logger.error(f"Failed to update cached_path for {mid}: {err}",
                         exc_info=True)
            return None


@huey.task()
async def fetch_songlist(songlist_id: int):
    return await _fetch_songlist_impl(songlist_id=songlist_id)


async def _fetch_songlist_impl(songlist_id: int):
    """
    使用 qqmusic_api 获取歌单信息。
    {
        "dirinfo": {
            "id": 9561851623,
            "host_uin": 939861972,
            "dirid": 8,
            "title": "崩铁角色+地区",
            "picurl": "https://music-file.y.qq.com/songlist/user/NKoqNeC5NKSA/68a3ff5b/HjXL0fy6EiixGOe8lSMEtc_190f80.jpg?imageView2/4/w/600/h/600",
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
    async with AsyncSessionLocal() as session:
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

            songlist = await create_or_update_songlist(
                session=session,
                platform="qq",
                platform_songlist_id=songlist_id,
                title=title,
                cover_url=dirinfo.get("picurl"),
                metadata_json=first_songlist,
                creator_name=dirinfo.get("host_nick"),
            )

            semaphore = asyncio.Semaphore(max(1, SONGLIST_FETCH_CONCURRENCY))

            async def fetch_page(page: int) -> list:
                async with semaphore:
                    for attempt in range(1, SONGLIST_FETCH_RETRIES + 1):
                        try:
                            songlist_page = await qapi.songlist.get_detail(
                                songlist_id, page=page)
                            return songlist_page["songlist"]
                        except Exception as page_err:
                            if attempt >= SONGLIST_FETCH_RETRIES:
                                logger.error(
                                    f"Failed to fetch page {page} of songlist {songlist_id} after {attempt} attempts: {page_err}"
                                )
                                raise
                            sleep_seconds = SONGLIST_FETCH_BACKOFF_SECONDS * (
                                2**(attempt - 1))
                            logger.warning(
                                f"Fetch page {page} failed on attempt {attempt}/{SONGLIST_FETCH_RETRIES}, retrying in {sleep_seconds:.2f}s: {page_err}"
                            )
                            await asyncio.sleep(sleep_seconds)

                return []

            total_pages = (total + 9) // 10
            pages = range(2, total_pages + 1)
            results = await asyncio.gather(
                *[fetch_page(page) for page in pages])
            for result in results:
                songs.extend(result)

            db_songs = await create_or_update_songs(
                session=session,
                songlist_id=songlist.id,
                songs=songs,
            )
            await session.commit()

            logger.info(
                f"Fetched songlist {songlist_id}: {title} with {total} songs, persisted {len(db_songs)} songs"
            )
            return db_songs, songlist
        except KeyError as e:
            await session.rollback()
            logger.error(f"KeyError fetching songlist: {e}", exc_info=True)
            return None
        except Exception as e:
            await session.rollback()
            logger.error(f"Error fetching songlist: {e}", exc_info=True)
            return None


# if __name__ == "__main__":
#     songlist_id = 9561851623
#     asyncio.run(fetch_songlist(songlist_id))
