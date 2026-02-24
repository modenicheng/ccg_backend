import json
import time
from typing import Any

from sqlalchemy import select

from cache.connection import redis_client
from cache.utils import RedisKeys, room_manager
from db.models import Room, Song, Songlist, User
from db.session import AsyncSessionLocal
from utils import get_logger
from utils.enumerations import EventType

from . import regist

logger = get_logger(__name__)


def _room_seq_key(room_id: str) -> str:
    return f"room:{room_id}:seq"


async def _next_seq(room_id: str) -> int:
    redis = await redis_client.get_client()
    if not redis:
        return 0
    return int(await redis.incr(_room_seq_key(room_id)))


async def _ws_envelope(event: EventType, room_id: str, data: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "v": 1,
        "event": event.value,
        "ts": int(time.time() * 1000),
        "seq": await _next_seq(room_id),
        "data": data,
    }
    if request_id:
        payload["request_id"] = request_id
    return payload


async def _get_username_map(room_id: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    try:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(select(User).where(User.room_id == room_id))
            for user in rows.scalars().all():
                mapping[user.player_id] = user.username
    except Exception as exc:
        logger.warning("Failed to query users for room %s: %s", room_id, exc)
    return mapping


def _extract_duration_ms(metadata: dict[str, Any] | None) -> int | None:
    if not isinstance(metadata, dict):
        return None

    duration_ms = metadata.get("duration_ms")
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        return int(duration_ms)

    interval = metadata.get("interval")
    if isinstance(interval, (int, float)) and interval > 0:
        return int(interval * 1000)

    return None


async def _build_media_state(room_id: str, current_song_id: int | None, total_songs: int) -> tuple[dict[str, Any], dict[str, Any]]:
    room_title: str | None = None
    playlist_id: str | None = None
    songlist: Songlist | None = None
    current_song: Song | None = None

    try:
        async with AsyncSessionLocal() as session:
            room = await session.get(Room, room_id)
            if room:
                room_title = room.title
                playlist_id = room.playlist_id

            if playlist_id:
                maybe_songlist: Songlist | None = None
                if str(playlist_id).isdigit():
                    maybe_songlist = await session.get(Songlist, int(playlist_id))

                if not maybe_songlist:
                    songlist_rows = await session.execute(
                        select(Songlist).where(Songlist.platform_songlist_id == str(playlist_id)),
                    )
                    maybe_songlist = songlist_rows.scalars().first()

                songlist = maybe_songlist

            if current_song_id is not None:
                current_song = await session.get(Song, current_song_id)
    except Exception as exc:
        logger.warning("Failed to query media state for room %s: %s", room_id, exc)

    songlist_payload = {
        "songlist_id": str(songlist.id) if songlist else (str(playlist_id) if playlist_id else None),
        "name": (songlist.title if songlist and songlist.title else room_title) or None,
        "cover_url": songlist.cover_url if songlist else None,
        "total_songs": max(0, int(total_songs)),
    }

    current_song_payload = {
        "song_id": str(current_song.id) if current_song else (str(current_song_id) if current_song_id is not None else None),
        "name": current_song.title if current_song else None,
        "artist": current_song.artist if current_song else None,
        "album": current_song.album_name if current_song else None,
        "cover_url": current_song.cover_url if current_song else None,
        "duration_ms": _extract_duration_ms(current_song.metadata_json) if current_song else None,
    }

    return songlist_payload, current_song_payload


async def build_room_state(room_id: str) -> dict[str, Any]:
    redis = await redis_client.get_client()
    if not redis:
        raise RuntimeError("Redis unavailable")

    room_data = await redis.hgetall(RedisKeys.room(room_id))
    if not room_data:
        raise ValueError(f"Room {room_id} not found")

    host_player_id = room_data.get("host_player_id", "")
    status = room_data.get("status", "waiting")
    current_song_index = int(room_data.get("current_song_index", 0) or 0)
    current_round_index = int(room_data.get("current_round_index", 0) or 0)
    current_song_id: int | None = None
    play_progress_ms = int(room_data.get("play_progress", 0) or 0)
    raw_play_state = room_data.get("current_round_state", "paused")
    play_state = "paused" if raw_play_state == "pause" else raw_play_state
    current_answerer_player_id = room_data.get("current_answerer") or None

    players = sorted(list(await room_manager.get_room_players(room_id)))
    ready_key = RedisKeys.room_ready(room_id)
    ready_map = await redis.hgetall(ready_key)
    username_map = await _get_username_map(room_id)

    song_queue = await room_manager.get_song_queue(room_id)
    if 0 <= current_song_index < len(song_queue):
        song_value = song_queue[current_song_index]
        try:
            current_song_id = int(song_value)
        except (TypeError, ValueError):
            current_song_id = None

    queue_player_ids = await room_manager.get_answer_queue(room_id)

    songlist_payload, current_song_payload = await _build_media_state(
        room_id,
        current_song_id,
        len(song_queue),
    )

    tag_groups_raw = room_data.get("tag_groups", "{}")
    tag_groups: list[dict[str, Any]] = []
    try:
        parsed = json.loads(tag_groups_raw) if tag_groups_raw else {}
        if isinstance(parsed, list):
            tag_groups = parsed
        elif isinstance(parsed, dict):
            maybe_groups = parsed.get("groups")
            if isinstance(maybe_groups, list):
                tag_groups = maybe_groups
    except json.JSONDecodeError:
        logger.warning("Invalid tag_groups JSON in room %s", room_id)

    players_payload = []
    for player_id in players:
        players_payload.append(
            {
                "player_id": player_id,
                "username": username_map.get(player_id, player_id),
                "is_host": player_id == host_player_id,
                "is_ready": ready_map.get(player_id, "false") == "true",
                "score": 0,
            }
        )

    return {
        "room_id": room_id,
        "status": status,
        "players": players_payload,
        "current_round_index": current_round_index,
        "current_song_index": current_song_index,
        "current_song_id": current_song_id,
        "play_state": play_state,
        "play_progress_ms": play_progress_ms,
        "queue_player_ids": queue_player_ids,
        "current_answerer_player_id": current_answerer_player_id,
        "tag_groups": tag_groups,
        "songlist": songlist_payload,
        "current_song": current_song_payload,
        "playback_context": {
            "current_song_index": current_song_index,
            "current_round_index": current_round_index,
            "total_rounds": len(song_queue),
        },
        "server_time_ms": int(time.time() * 1000),
    }


async def broadcast_room_state(room_id: str, clients, request_id: str | None = None):
    if not clients:
        return
    state = await build_room_state(room_id)
    envelope = await _ws_envelope(EventType.ROOM_STATE, room_id, state, request_id=request_id)
    await clients.broadcast(room_id, envelope)


async def send_room_state(room_id: str, clients, websocket, request_id: str | None = None):
    if not clients or not websocket:
        return
    state = await build_room_state(room_id)
    envelope = await _ws_envelope(EventType.ROOM_STATE, room_id, state, request_id=request_id)
    await clients.send(websocket, envelope)


async def broadcast_answer_queue(room_id: str, clients, request_id: str | None = None):
    queue = await room_manager.get_answer_queue(room_id)
    payload = {
        "room_id": room_id,
        "queue_player_ids": queue,
    }
    envelope = await _ws_envelope(EventType.ANSWER_QUEUE, room_id, payload, request_id=request_id)
    await clients.broadcast(room_id, envelope)


@regist(EventType.PLAYER_READY)
async def handle_player_ready(
    data: dict[str, Any],
    clients=None,
    websocket=None,
    room_id: str | None = None,
    player_id: str | None = None,
):
    if not room_id or not player_id:
        return
    is_ready = bool(data.get("is_ready", False))
    await room_manager.set_player_ready(room_id, player_id, is_ready)
    await broadcast_room_state(room_id, clients, request_id=data.get("request_id"))


@regist(EventType.ATTEMPT_ANSWER)
async def handle_attempt_answer(
    data: dict[str, Any],
    clients=None,
    websocket=None,
    room_id: str | None = None,
    player_id: str | None = None,
):
    if not room_id or not player_id:
        return

    progress_ms = int(data.get("progress_ms", 0) or 0)
    await room_manager.update_play_progress(room_id, progress_ms)
    await room_manager.add_to_answer_queue(room_id, player_id)

    queue = await room_manager.get_answer_queue(room_id)
    if queue and queue[0] == player_id:
        await room_manager.set_current_answerer(room_id, player_id)
        redis = await redis_client.get_client()
        if redis:
            await redis.hset(RedisKeys.room(room_id), mapping={"current_round_state": "paused"})

        pause_payload = {
            "room_id": room_id,
            "song_id": None,
            "progress_ms": progress_ms,
            "reason": "attempt_answer",
            "trigger_player_id": player_id,
        }
        pause_envelope = await _ws_envelope(EventType.PAUSE, room_id, pause_payload, request_id=data.get("request_id"))
        if clients:
            await clients.broadcast(room_id, pause_envelope)
        else:
            logger.warning("No clients manager available to broadcast pause event for attempt_answer")

    await broadcast_answer_queue(room_id, clients, request_id=data.get("request_id"))
    await broadcast_room_state(room_id, clients, request_id=data.get("request_id"))


@regist(EventType.SEEK)
async def handle_seek(
    data: dict[str, Any],
    clients=None,
    websocket=None,
    room_id: str | None = None,
    player_id: str | None = None,
):
    if not room_id:
        return

    progress_ms = int(data.get("progress_ms", 0) or 0)
    keep_playing = bool(data.get("keep_playing", False))

    await room_manager.update_play_progress(room_id, progress_ms)
    redis = await redis_client.get_client()
    if redis:
        await redis.hset(
            RedisKeys.room(room_id),
            mapping={
                "current_round_state": "playing" if keep_playing else "paused",
            },
        )

    payload = {
        "room_id": room_id,
        "song_id": data.get("song_id"),
        "progress_ms": progress_ms,
        "keep_playing": keep_playing,
    }
    envelope = await _ws_envelope(EventType.SEEK, room_id, payload, request_id=data.get("request_id"))
    if clients:
        await clients.broadcast(room_id, envelope)
    await broadcast_room_state(room_id, clients, request_id=data.get("request_id"))


@regist(EventType.PLAY)
async def handle_play(
    data: dict[str, Any],
    clients=None,
    websocket=None,
    room_id: str | None = None,
    player_id: str | None = None,
):
    if not room_id:
        return

    progress_ms = int(data.get("progress_ms", 0) or 0)
    await room_manager.update_play_progress(room_id, progress_ms)

    redis = await redis_client.get_client()
    if redis:
        await redis.hset(RedisKeys.room(room_id), mapping={"current_round_state": "playing"})

    payload = {
        "room_id": room_id,
        "song_id": data.get("song_id"),
        "progress_ms": progress_ms,
    }
    envelope = await _ws_envelope(EventType.PLAY, room_id, payload, request_id=data.get("request_id"))
    if clients:
        await clients.broadcast(room_id, envelope)
    await broadcast_room_state(room_id, clients, request_id=data.get("request_id"))


@regist(EventType.PAUSE)
async def handle_pause(
    data: dict[str, Any],
    clients=None,
    websocket=None,
    room_id: str | None = None,
    player_id: str | None = None,
):
    if not room_id:
        return

    progress_ms = int(data.get("progress_ms", 0) or 0)
    await room_manager.update_play_progress(room_id, progress_ms)

    redis = await redis_client.get_client()
    if redis:
        await redis.hset(RedisKeys.room(room_id), mapping={"current_round_state": "paused"})

    payload = {
        "room_id": room_id,
        "song_id": data.get("song_id"),
        "progress_ms": progress_ms,
        "reason": data.get("reason", "manual"),
        "trigger_player_id": player_id,
    }
    envelope = await _ws_envelope(EventType.PAUSE, room_id, payload, request_id=data.get("request_id"))
    if clients:
        await clients.broadcast(room_id, envelope)
    await broadcast_room_state(room_id, clients, request_id=data.get("request_id"))


@regist(EventType.SCORE_UPDATE)
async def handle_score_update(
    data: dict[str, Any],
    clients=None,
    websocket=None,
    room_id: str | None = None,
    player_id: str | None = None,
):
    if not room_id:
        return

    payload = {
        "room_id": room_id,
        "round_index": int(data.get("round_index", 0) or 0),
        "scores": data.get("scores", []),
    }
    envelope = await _ws_envelope(EventType.SCORE_UPDATE, room_id, payload, request_id=data.get("request_id"))
    if clients:
        await clients.broadcast(room_id, envelope)
    await broadcast_room_state(room_id, clients, request_id=data.get("request_id"))


@regist(EventType.YOUR_TURN)
async def handle_your_turn(
    data: dict[str, Any],
    clients=None,
    websocket=None,
    room_id: str | None = None,
    player_id: str | None = None,
):
    if not room_id:
        return

    answerer_player_id = data.get("answerer_player_id")
    if not answerer_player_id:
        return

    payload = {
        "room_id": room_id,
        "song_id": data.get("song_id"),
        "answerer_player_id": answerer_player_id,
        "time_limit_sec": int(data.get("time_limit_sec", 30) or 30),
        "queue_position": int(data.get("queue_position", 1) or 1),
        "tag_groups": data.get("tag_groups", []),
    }

    envelope = await _ws_envelope(EventType.YOUR_TURN, room_id, payload, request_id=data.get("request_id"))

    # single cast to answerer
    if not clients:
        logger.warning("No clients manager available to send YOUR_TURN event")
        return
    for client in clients.get_clients(room_id):
        if client is websocket and player_id == answerer_player_id:
            await clients.send(client, envelope)
            break

    await broadcast_room_state(room_id, clients, request_id=data.get("request_id"))
