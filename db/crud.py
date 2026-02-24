from typing import Any, Literal, Optional

from sqlalchemy import and_, select, tuple_
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


# ===================== 房间持久化相关 CRUD 操作 =====================

async def create_room_in_db(session: AsyncSession, room_id: str, title: Optional[str] = None, 
                           description: Optional[str] = None) -> Optional[models.Room]:
    """创建房间记录到数据库
    
    Args:
        session: 数据库会话
        room_id: 房间 ID
        title: 房间标题
        description: 房间描述
        
    Returns:
        创建的 Room 对象，或 None 如果失败
    """
    try:
        from datetime import datetime
        
        # 检查房间是否已存在
        existing = await session.get(models.Room, room_id)
        if existing:
            l.warning(f"Room {room_id} already exists in database")
            return existing
        
        room = models.Room(
            id=room_id,
            title=title or "New Room",
            description=description or "",
            status="waiting",
            created_at=datetime.now()
        )
        session.add(room)
        await session.flush()
        return room
    except Exception as e:
        l.error(f"Error creating room in database: {e}")
        return None


async def add_user_to_room(session: AsyncSession, room_id: str, player_id: str, username: str, 
                          is_owner: bool = False) -> Optional[models.User]:
    """将用户添加到房间
    
    Args:
        session: 数据库会话
        room_id: 房间 ID
        player_id: 玩家 ID（系统唯一）
        username: 显示用户名（房间内唯一）
        is_owner: 是否是房主
        
    Returns:
        创建的 User 对象，或 None 如果失败
    """
    try:
        # 优先按 player_id 查找（稳定身份键）
        query = select(models.User).where(models.User.player_id == player_id)
        res = await session.execute(query)
        existing_user = res.scalars().first()
        
        if existing_user:
            existing_user.room_id = room_id
            existing_user.username = username
            existing_user.is_owner = is_owner
            await session.flush()
            return existing_user

        # 房间内用户名唯一
        same_name_query = select(models.User).where(
            and_(models.User.room_id == room_id, models.User.username == username)
        )
        same_name_res = await session.execute(same_name_query)
        if same_name_res.scalars().first():
            l.warning(f"Duplicate username in room: room_id={room_id}, username={username}")
            return None
        
        # 创建新用户
        user = models.User(
            player_id=player_id,
            username=username,
            room_id=room_id,
            is_owner=is_owner
        )
        session.add(user)
        await session.flush()
        return user
    except Exception as e:
        l.error(f"Error adding user to room: {e}")
        return None


async def update_room_status(session: AsyncSession, room_id: str, status: str) -> bool:
    """更新房间状态
    
    Args:
        session: 数据库会话
        room_id: 房间 ID
        status: 新状态 ("waiting", "playing", "ended")
        
    Returns:
        是否更新成功
    """
    try:
        room = await session.get(models.Room, room_id)
        if not room:
            l.warning(f"Room {room_id} not found")
            return False
        
        room.status = status
        if status == "playing" and not room.started_at:
            from datetime import datetime
            room.started_at = datetime.now()
        elif status == "ended" and not room.ended_at:
            from datetime import datetime
            room.ended_at = datetime.now()
        
        await session.flush()
        return True
    except Exception as e:
        l.error(f"Error updating room status: {e}")
        return False


async def update_room_scores(session: AsyncSession, room_id: str, 
                            final_scores: dict[str, Any]) -> bool:
    """更新房间的最终积分
    
    Args:
        session: 数据库会话
        room_id: 房间 ID
        final_scores: 最终积分字典
        
    Returns:
        是否更新成功
    """
    try:
        room = await session.get(models.Room, room_id)
        if not room:
            l.warning(f"Room {room_id} not found")
            return False
        
        room.final_scores_json = final_scores
        await session.flush()
        return True
    except Exception as e:
        l.error(f"Error updating room scores: {e}")
        return False


async def update_room_rounds_data(session: AsyncSession, room_id: str, 
                                 rounds_data: dict[str, Any]) -> bool:
    """更新房间的轮次数据
    
    Args:
        session: 数据库会话
        room_id: 房间 ID
        rounds_data: 轮次数据
        
    Returns:
        是否更新成功
    """
    try:
        room = await session.get(models.Room, room_id)
        if not room:
            l.warning(f"Room {room_id} not found")
            return False
        
        room.rounds_data = rounds_data
        await session.flush()
        return True
    except Exception as e:
        l.error(f"Error updating room rounds data: {e}")
        return False


async def mark_user_left_room(session: AsyncSession, user_id: int) -> bool:
    """标记用户离开房间
    
    Args:
        session: 数据库会话
        user_id: 用户 ID
        
    Returns:
        是否更新成功
    """
    try:
        from datetime import datetime
        
        user = await session.get(models.User, user_id)
        if not user:
            l.warning(f"User {user_id} not found")
            return False
        
        user.left_at = datetime.now()
        user.room_id = None
        await session.flush()
        return True
    except Exception as e:
        l.error(f"Error marking user left room: {e}")
        return False

