from typing import Any, Literal, Optional

from sqlalchemy import and_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from utils import logger

from . import models
from utils.enumerations import MusicPlatform

l = logger.get_logger(__name__)

def _apply_songlist_fields(songlist: models.Songlist,
                           title: Optional[str],
                           creator_name: Optional[str],
                           cover_url: Optional[str],
                           metadata_json: Optional[dict[str, Any]]) -> None:
    songlist.title = title
    songlist.creator_name = creator_name
    songlist.cover_url = cover_url
    songlist.metadata_json = metadata_json


def _apply_song_fields(song: models.Song,
                       title: Optional[str],
                       subtitle: Optional[str],
                       artist: Optional[str],
                       cover_url: Optional[str],
                       audio_url: Optional[str],
                       cached_path: Optional[str],
                       album_name: Optional[str],
                       metadata_json: Optional[dict[str, Any]]) -> None:
    song.title = title
    song.subtitle = subtitle
    song.artist = artist
    song.cover_url = cover_url
    song.audio_url = audio_url
    song.cached_path = cached_path
    song.album_name = album_name
    song.metadata_json = metadata_json


def _to_platform_value(platform: Any) -> Optional[str]:
    if isinstance(platform, MusicPlatform):
        return platform.value
    return platform


def _join_singer_names(raw_singers: Any) -> Optional[str]:
    if isinstance(raw_singers, list):
        names = [
            str(item.get("name")) for item in raw_singers
            if isinstance(item, dict) and item.get("name")
        ]
        return "/".join(names) if names else None
    if isinstance(raw_singers, str):
        return raw_singers
    return None


def _normalize_song_input(song_data: dict[str, Any]) -> Optional[dict[str, Any]]:
    platform = _to_platform_value(song_data.get("platform") or "qq")

    platform_song_id = (
        song_data.get("platform_song_id")
        or song_data.get("songmid")
        or song_data.get("mid")
        or song_data.get("id")
    )
    if platform_song_id is None:
        return None
    platform_song_id = str(platform_song_id)

    album_raw = song_data.get("album")
    album_info: dict[str, Any] = album_raw if isinstance(album_raw, dict) else {}

    return {
        "platform": platform,
        "platform_song_id": platform_song_id,
        "title": song_data.get("title") or song_data.get("songname") or song_data.get("name"),
        "subtitle": song_data.get("subtitle") or song_data.get("subTitle") or song_data.get("subtitle_name"),
        "artist": song_data.get("artist") or _join_singer_names(song_data.get("singer")),
        "cover_url": song_data.get("cover_url") or song_data.get("cover") or song_data.get("picurl"),
        "audio_url": song_data.get("audio_url") or song_data.get("url"),
        "cached_path": song_data.get("cached_path"),
        "album_name": song_data.get("album_name") or song_data.get("albumname") or album_info.get("name"),
        "metadata_json": song_data.get("metadata_json") if "metadata_json" in song_data else song_data,
    }


async def create_or_update_songlist(session: AsyncSession,
                          platform: Optional[Literal["qq", "netease"]],
                          platform_songlist_id: Optional[int | str],
                          title: Optional[str] = None,
                          creator_name: Optional[str] = None,
                          cover_url: Optional[str] = None,
                          metadata_json: Optional[dict[str, Any]] = None):
    """创建新的歌单记录"""
    normalized_platform_songlist_id = (
        str(platform_songlist_id) if platform_songlist_id is not None else None)

    query = select(models.Songlist).where(
        and_(models.Songlist.platform_songlist_id == normalized_platform_songlist_id,
             models.Songlist.platform == platform))
    res = await session.execute(query)
    existing = res.scalars().first()
    if existing:
        _apply_songlist_fields(existing,
                               title=title,
                               creator_name=creator_name,
                               cover_url=cover_url,
                               metadata_json=metadata_json)
        await session.flush()
        return existing

    songlist = models.Songlist(platform=platform,
                               platform_songlist_id=normalized_platform_songlist_id)
    _apply_songlist_fields(songlist,
                           title=title,
                           creator_name=creator_name,
                           cover_url=cover_url,
                           metadata_json=metadata_json)

    session.add(songlist)
    await session.flush()
    return songlist


async def create_or_update_song(session: AsyncSession,
                                songlist_id: Optional[int],
                                platform: Optional[Literal["qq", "netease"]],
                                platform_song_id: Optional[str],
                                title: Optional[str] = None,
                                subtitle: Optional[str] = None,
                                artist: Optional[str] = None,
                                cover_url: Optional[str] = None,
                                audio_url: Optional[str] = None,
                                cached_path: Optional[str] = None,
                                album_name: Optional[str] = None,
                                metadata_json: Optional[dict[str,
                                                             Any]] = None):
    """创建或更新歌曲记录"""
    query = select(models.Song).where(
        and_(models.Song.platform_song_id == platform_song_id,
             models.Song.platform == platform))
    res = await session.execute(query)
    existing = res.scalars().first()

    async def ensure_songlist_link(song_id: int) -> None:
        if not songlist_id:
            return
        relation_query = select(models.SonglistSong).where(
            and_(models.SonglistSong.songlist_id == songlist_id,
                 models.SonglistSong.song_id == song_id))
        relation_res = await session.execute(relation_query)
        relation = relation_res.scalars().first()
        if relation:
            return

        session.add(models.SonglistSong(songlist_id=songlist_id, song_id=song_id))

    if existing:
        _apply_song_fields(existing,
                           title=title,
                           subtitle=subtitle,
                           artist=artist,
                           cover_url=cover_url,
                           audio_url=audio_url,
                           cached_path=cached_path,
                           album_name=album_name,
                           metadata_json=metadata_json)
        await ensure_songlist_link(existing.id)
        await session.flush()
        return existing

    song = models.Song(platform=platform,
                       platform_song_id=platform_song_id)
    _apply_song_fields(song,
                       title=title,
                       subtitle=subtitle,
                       artist=artist,
                       cover_url=cover_url,
                       audio_url=audio_url,
                       cached_path=cached_path,
                       album_name=album_name,
                       metadata_json=metadata_json)

    session.add(song)
    await session.flush()
    await ensure_songlist_link(song.id)
    await session.flush()
    return song


async def create_or_update_songs(session: AsyncSession,
                                  songlist_id: Optional[int],
                                songs: list[dict[str, Any]]) -> list[models.Song]:
    """批量创建或更新歌曲记录"""
    if not songs:
        return []

    ordered_keys: list[tuple[str, str]] = []
    song_payload_map: dict[tuple[str, str], dict[str, Any]] = {}

    for raw_song in songs:
        if not isinstance(raw_song, dict):
            continue
        payload = _normalize_song_input(raw_song)
        if not payload:
            continue

        key = (payload["platform"], payload["platform_song_id"])
        if key not in song_payload_map:
            ordered_keys.append(key)
        song_payload_map[key] = payload

    if not ordered_keys:
        return []

    existing_query = select(models.Song).where(
        tuple_(models.Song.platform, models.Song.platform_song_id).in_(ordered_keys))
    existing_res = await session.execute(existing_query)
    existing_songs = existing_res.scalars().all()
    song_map: dict[tuple[str, str], models.Song] = {
        (song.platform, song.platform_song_id): song
        for song in existing_songs
        if song.platform is not None and song.platform_song_id is not None
    }

    new_songs: list[models.Song] = []
    result_songs: list[models.Song] = []

    for key in ordered_keys:
        payload = song_payload_map[key]
        existing_song = song_map.get(key)

        if existing_song:
            _apply_song_fields(existing_song,
                               title=payload["title"],
                               subtitle=payload["subtitle"],
                               artist=payload["artist"],
                               cover_url=payload["cover_url"],
                               audio_url=payload["audio_url"],
                               cached_path=payload["cached_path"],
                               album_name=payload["album_name"],
                               metadata_json=payload["metadata_json"])
            result_songs.append(existing_song)
            continue

        song = models.Song(platform=payload["platform"],
                           platform_song_id=payload["platform_song_id"])
        _apply_song_fields(song,
                           title=payload["title"],
                           subtitle=payload["subtitle"],
                           artist=payload["artist"],
                           cover_url=payload["cover_url"],
                           audio_url=payload["audio_url"],
                           cached_path=payload["cached_path"],
                           album_name=payload["album_name"],
                           metadata_json=payload["metadata_json"])
        new_songs.append(song)
        result_songs.append(song)

    if new_songs:
        session.add_all(new_songs)

    await session.flush()

    if songlist_id:
        song_ids = [song.id for song in result_songs if song.id]
        if song_ids:
            relation_query = select(models.SonglistSong).where(
                and_(models.SonglistSong.songlist_id == songlist_id,
                     models.SonglistSong.song_id.in_(song_ids)))
            relation_res = await session.execute(relation_query)
            existing_relations = relation_res.scalars().all()
            existing_song_ids = {relation.song_id for relation in existing_relations}

            new_relations = [
                models.SonglistSong(songlist_id=songlist_id, song_id=song_id)
                for song_id in song_ids
                if song_id not in existing_song_ids
            ]
            if new_relations:
                session.add_all(new_relations)
                await session.flush()

    return result_songs


async def update_song_cached_path(session: AsyncSession,
                                  platform: Optional[Literal["qq", "netease"]],
                                  platform_song_id: Optional[str],
                                  cached_path: Optional[str]) -> Optional[models.Song]:
    """仅更新歌曲缓存路径，不覆盖其它业务字段。"""
    if not platform or not platform_song_id:
        l.warning(f"Missing platform or platform_song_id for cache path update: {platform}, {platform_song_id}, skip updating")
        return None

    query = select(models.Song).where(
        and_(models.Song.platform_song_id == platform_song_id,
             models.Song.platform == platform))
    res = await session.execute(query)
    existing = res.scalars().first()
    if not existing:
        return None

    existing.cached_path = cached_path
    await session.flush()
    return existing


async def create_task_record(session: AsyncSession,
                             task_id: str,
                             task_name: str,
                             status: str,
                             result_json: Optional[dict[str, Any]] = None
                             ) -> models.Tasks:
    """创建任务记录。并发场景下使用 UPSERT 防止 task_id 唯一键冲突。"""
    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""

    if dialect_name == "postgresql":
        stmt = pg_insert(models.Tasks).values(
            task_id=task_id,
            task_name=task_name,
            status=status,
            result_json=result_json,
        ).on_conflict_do_update(
            index_elements=[models.Tasks.task_id],
            set_={
                "task_name": task_name,
                "status": status,
                "result_json": result_json,
            },
        )
        await session.execute(stmt)
        task = await get_task_record_by_task_id(session=session, task_id=task_id)
        assert task is not None
        return task

    query = select(models.Tasks).where(models.Tasks.task_id == task_id)
    res = await session.execute(query)
    existing = res.scalars().first()
    if existing:
        existing.task_name = task_name
        existing.status = status
        existing.result_json = result_json
        await session.flush()
        return existing

    task = models.Tasks(task_id=task_id,
                        task_name=task_name,
                        status=status,
                        result_json=result_json)
    session.add(task)
    try:
        await session.flush()
        return task
    except IntegrityError:
        await session.rollback()
        res = await session.execute(query)
        existing = res.scalars().first()
        if not existing:
            raise
        existing.task_name = task_name
        existing.status = status
        existing.result_json = result_json
        await session.flush()
        return existing


async def get_task_record_by_task_id(session: AsyncSession,
                                     task_id: str) -> Optional[models.Tasks]:
    """按 Huey task_id 查询任务记录。"""
    query = select(models.Tasks).where(models.Tasks.task_id == task_id)
    res = await session.execute(query)
    return res.scalars().first()


async def update_task_record(session: AsyncSession,
                             task_id: str,
                             status: Optional[str] = None,
                             result_json: Optional[dict[str, Any]] = None
                             ) -> Optional[models.Tasks]:
    """更新任务状态与结果。"""
    task = await get_task_record_by_task_id(session=session, task_id=task_id)
    if not task:
        return None

    if status is not None:
        task.status = status
    if result_json is not None:
        task.result_json = result_json

    await session.flush()
    return task
