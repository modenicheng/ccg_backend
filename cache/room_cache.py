"""Redis cache module for playback and answer-queue state."""

from __future__ import annotations

from functools import update_wrapper
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar, cast, Concatenate

from pydantic import ValidationError
from redis.asyncio.client import Redis

from db.models import Room, RoomStatusORM
from db.session import session_scope
from db.crud.room_state_related import (
    get_room_playback_state_json,
    get_room_current_song_index,
    set_room_playback_state_json,
)
from db.crud.judge_related import get_current_song_info
from db.crud.audio_preload_and_token import get_or_create_audio_token
from utils.audio_token import get_audio_stream_url
from schemas.ws_messages import room_schemas as RoomSchemas
from utils import get_logger

from .connection import get_redis
from .schemas import AnswerQueueItem, PlaybackState
from .utils import RedisKeys, ROOM_TTL_SECONDS

logger = get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def handle_redis_operation(
    default_return: Any = None,
    log_operation: str = "",
    reraise_exceptions: tuple[type[Exception], ...] = (),
) -> Callable[[Callable[Concatenate[Redis, P], Awaitable[R]]], Callable[P,
                                                                        Awaitable[R]]]:
    """
    处理Redis操作通用逻辑的装饰器

    被装饰的函数应该接受redis连接作为第一个参数，其他参数保持不变。
    装饰器会自动获取redis连接，处理异常，并记录日志。

    Args:
        default_return: 发生异常时返回的默认值
        log_operation: 操作描述，用于日志（如"loading room state"）
        reraise_exceptions: 需要重新抛出的异常类型元组
    """

    def decorator(
        func: Callable[Concatenate[Redis, P],
                       Awaitable[R]],) -> Callable[P, Awaitable[R]]:

        async def wrapper(  # pylint: disable=too-many-branches
                *args: P.args, **kwargs: P.kwargs) -> R:
            try:
                redis = await get_redis()
                # 注意：原函数现在需要接受redis作为第一个参数
                result = await func(redis, *args, **kwargs)

                # 记录成功日志
                room_id = None
                for arg in args:
                    if isinstance(arg, str) and len(arg) > 0:
                        room_id = arg
                        break

                if room_id:
                    if log_operation:
                        logger.debug("%s for room %s", log_operation, room_id)
                    else:
                        logger.debug("Operation completed for room %s", room_id)
                else:
                    if log_operation:
                        logger.debug("%s completed", log_operation)
                    else:
                        logger.debug("Operation completed")

                return result
            except Exception as e:  # pylint: disable=broad-exception-caught
                # 检查是否需要重新抛出异常
                if reraise_exceptions and any(
                        isinstance(e, exc_type) for exc_type in reraise_exceptions):
                    raise e

                # 提取room_id用于错误日志
                room_id = None
                for arg in args:
                    if isinstance(arg, str) and len(arg) > 0:
                        room_id = arg
                        break

                if room_id:
                    if log_operation:
                        logger.error(
                            "Error %s for room %s: %s",
                            log_operation,
                            room_id,
                            e,
                            exc_info=True,
                        )
                    else:
                        logger.error(
                            "Error for room %s: %s",
                            room_id,
                            e,
                            exc_info=True,
                        )
                else:
                    if log_operation:
                        logger.error(
                            "Error %s: %s",
                            log_operation,
                            e,
                            exc_info=True,
                        )
                    else:
                        logger.error("Error: %s", e, exc_info=True)

                return cast(R, default_return)

        return update_wrapper(wrapper, func)

    return decorator


def _normalize_status_value(raw_status: str | None) -> str | None:
    """Normalize legacy room status values to canonical numeric string.

    Deprecated: 仅用于兼容旧缓存与单元测试；生产路径优先使用
    `cache.schemas.RoomBaseStateCache.normalize_status`。
    """
    if raw_status is None:
        return None

    value = str(raw_status).strip()
    if not value:
        return None

    if value.isdigit():
        try:
            return str(RoomStatusORM(int(value)).value)
        except ValueError:
            return str(RoomStatusORM.WAITING.value)

    upper = value.upper()
    if upper.startswith("ROOMSTATUSORM."):
        upper = upper.split(".", 1)[1]
    elif upper.startswith("ROOMSTATUS."):
        upper = upper.split(".", 1)[1]

    if upper in RoomStatusORM.__members__:
        return str(RoomStatusORM[upper].value)

    return str(RoomStatusORM.WAITING.value)


async def _load_playback_state_from_db(room_id: str) -> PlaybackState | None:
    """从数据库加载持久化播放状态。"""
    try:
        async with session_scope() as db:
            state_dict = await get_room_playback_state_json(db, room_id)
        if state_dict is None:
            return None
        return PlaybackState.model_validate(state_dict)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to load playback state from DB for room %s: %s", room_id,
                       e)
        return None


async def _save_playback_state_to_db(room_id: str, state: PlaybackState) -> None:
    """将播放状态持久化到数据库。"""
    try:
        async with session_scope() as db:
            await set_room_playback_state_json(db, room_id, state.model_dump())
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to persist playback state to DB for room %s: %s",
                       room_id, e)


async def _generate_default_playback_state(room_id: str) -> PlaybackState | None:
    """依据 current_song_index 生成默认播放状态（暂停，进度归零，填充临时播放 URL）。

    若房间处于 WAITING 或 ENDED 状态则返回 None，不生成播放状态。
    """
    current_order = 0
    audio_url: str | None = None
    try:
        async with session_scope() as db:
            room = await db.get(Room, room_id)
            if room is None:
                return None
            if RoomStatusORM(room.status) in (RoomStatusORM.WAITING,
                                              RoomStatusORM.ENDED):
                logger.debug(
                    "Room %s is in status %s, skipping default playback state generation",
                    room_id, room.status)
                return None
            song_id, song_index = await get_current_song_info(db, room_id)
            if song_id is not None and song_index is not None:
                current_order = song_index
                token = await get_or_create_audio_token(db, room_id, song_id)
                audio_url = get_audio_stream_url(token)
            else:
                # 没有当前歌曲时仍尝试获取 index 作为 current_order
                idx = await get_room_current_song_index(db, room_id)
                if idx is not None:
                    current_order = idx
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to generate default playback state for room %s: %s",
                       room_id, e)
        return None
    return PlaybackState(
        progress_ms=0,
        offset_ts=0,
        play_state="paused",
        current_order=current_order,
        audio_url=audio_url,
        show_answer=getattr(room, "show_answer", False),
    )


@handle_redis_operation(default_return=None, log_operation="saving playback state")
async def set_room_playback_state(redis: Redis, room_id: str,
                                  playback_state: PlaybackState) -> None:
    """将房间播放状态保存到 Redis，并异步持久化到数据库。

    Args:
        redis: Redis连接
        room_id (str): 房间 ID
        playback_state (PlaybackState): 播放状态对象
    """
    key = RedisKeys.playback_state(room_id)
    await cast(Awaitable, redis.hset(key, mapping=playback_state.to_redis_hash()))
    await cast(Awaitable, redis.expire(key, ROOM_TTL_SECONDS))
    # 同步持久化到 DB，确保 Redis 驱逐后仍可恢复
    await _save_playback_state_to_db(room_id, playback_state)


@handle_redis_operation(default_return=None, log_operation="updating playback progress")
async def set_room_playback_progress(redis: Redis, room_id: str, progress_ms: int,
                                     offset_ts: int) -> None:
    """更新房间播放进度（仅 progress_ms 和 offset_ts）

    Args:
        redis: Redis连接
        room_id (str): 房间 ID
        progress_ms (int): 当前播放进度（毫秒）
        offset_ts (int): 服务器时间戳（毫秒）
    """
    key = RedisKeys.playback_state(room_id)
    await cast(
        Awaitable,
        redis.hset(
            key,
            mapping={
                "progress_ms": str(progress_ms),
                "offset_ts": str(offset_ts),
            },
        ),
    )
    await cast(Awaitable, redis.expire(key, ROOM_TTL_SECONDS))


@handle_redis_operation(default_return=None, log_operation="loading playback state")
async def get_room_playback_state(redis: Redis, room_id: str) -> PlaybackState | None:
    """获取房间播放状态

    Args:
        redis: Redis连接
        room_id (str): 房间 ID

    Returns:
        PlaybackState | None: 播放状态对象，如果未找到则返回 None
    """
    key = RedisKeys.playback_state(room_id)
    data = await cast(Awaitable[dict], redis.hgetall(key))
    if data:
        return PlaybackState.from_redis_hash(data)

    # Cache miss：尝试从数据库恢复
    logger.debug("Playback state cache miss for room %s, checking DB", room_id)
    db_state = await _load_playback_state_from_db(room_id)
    if db_state is not None:
        # 回填 Redis
        await cast(Awaitable, redis.hset(key, mapping=db_state.to_redis_hash()))
        await cast(Awaitable, redis.expire(key, ROOM_TTL_SECONDS))
        return db_state

    # DB 也没有：依据 current_song_index 生成默认状态（仅 RUNNING 房间）
    logger.debug("No playback state in DB for room %s, generating default", room_id)
    return await _generate_default_playback_state(room_id)


@handle_redis_operation(default_return=None, log_operation="deleting playback state")
async def delete_room_playback_state(redis: Redis, room_id: str) -> None:
    """删除房间播放状态

    Args:
        redis: Redis连接
        room_id (str): 房间 ID
    """
    key = RedisKeys.playback_state(room_id)
    await cast(Awaitable, redis.delete(key))


# 以下这三个方法是比较核心的，涉及答题队列的维护（利用 Redis 有序集合的特性）
@handle_redis_operation(
    default_return=None,
    log_operation="appending player to answer queue",
    reraise_exceptions=(ValueError,),
)
async def append_attempt_answer_player(redis: Redis, room_id: str,
                                       data: AnswerQueueItem) -> int | None:
    """
    Asynchronously appends a player's answer attempt to the answer queue for a given room in Redis.

    Each player's attempt is serialized as a JSON string and added to a sorted set,
    where the score is calculated as `server_ts * 0.001 + offset_ts`. This scoring ensures unique
    ordering and high time precision, preventing conflicts when multiple players submit answers
    within the same millisecond.

    Args:
        redis: Redis connection
        room_id (str): The unique identifier of the room.
        data (AnswerQueueItem): The player's answer attempt data, including timestamps and player
                                information.

    Returns:
        int or None: The result of the Redis ZADD operation (number of elements added), or None if
        an error occurs.

    Raises:
        ValueError: If the player is already in the answer queue (business logic error).
    """
    key = RedisKeys.answer_queue(room_id)
    index_key = RedisKeys.answer_queue_player_index(room_id)
    player_id_key = str(data.player_id)
    member_key = data.model_dump_json()  # 将整个对象序列化为 JSON 字符串作为成员值
    # 将玩家添加到答题队列（有序集合），score 为 server_ts * 0.0001 + offset_ts
    # 即以 offset_ts 为主，server_ts 作为微调，确保同一毫秒内的玩家顺序由服务器时间决定
    # 这样可以保证队列顺序的唯一性和时间精度，防止同一毫秒内多玩家并发导致顺序冲突
    # 使用 Hash 索引避免逐条 JSON 反序列化检查重复 player_id
    reserve_ok = await cast(Awaitable[int],
                            redis.hsetnx(index_key, player_id_key, member_key))
    if reserve_ok == 0:
        logger.warning(
            "Player %s is already in the answer queue for room %s, skipping append",
            data.player_id,
            room_id,
        )
        raise ValueError(f"Player {data.player_id} is already in the answer queue")

    try:
        # Then add to queue
        result = await cast(
            Awaitable[int],
            redis.zadd(key, {member_key: data.server_ts * 0.0001 + data.offset_ts}),
        )
        await cast(Awaitable[bool], redis.expire(key, ROOM_TTL_SECONDS))
        await cast(Awaitable[bool], redis.expire(index_key, ROOM_TTL_SECONDS))
        return result
    except Exception:
        # 回滚 player_id 索引，避免索引脏数据阻塞后续抢答
        await cast(Awaitable[int], redis.hdel(index_key, player_id_key))
        raise


@handle_redis_operation(default_return=[], log_operation="getting answer queue")
async def get_answer_queue(redis: Redis,
                           room_id: str) -> list[RoomSchemas.AnswerQueueItem]:
    """
    Retrieve the answer queue for a given room from Redis.

    Args:
        redis: Redis connection
        room_id (str): The unique identifier of the room.

    Returns:
        list[AnswerQueueItem]: A list of AnswerQueueItem objects representing the players
                               in the answer queue, ordered by their offset timestamp
                               (score in the sorted set). If an error occurs, returns an
                               empty list.
    """
    key = RedisKeys.answer_queue(room_id)
    # 获取有序集合中的所有玩家ID（offset_ts）
    entries = await cast(Awaitable[list], redis.zrange(key, 0, -1))
    answer_queue = [
        RoomSchemas.AnswerQueueItem.model_validate_json(
            p, strict=False).model_copy(update={"order": o + 1})
        for o, p in enumerate(entries)
    ]
    return answer_queue


@handle_redis_operation(default_return=None, log_operation="clearing answer queue")
async def clear_answer_queue(redis: Redis, room_id: str) -> int | None:
    """
    Asynchronously clears the answer queue for a given room in Redis.

    Args:
        redis: Redis连接
        room_id (str): The unique identifier of the room whose answer queue should be cleared.

    Returns:
        int or None: The number of keys that were removed from Redis, or None if an error occurred.
    """
    key = RedisKeys.answer_queue(room_id)
    index_key = RedisKeys.answer_queue_player_index(room_id)
    tail_key = RedisKeys.answer_queue_tail_player(room_id)
    result = await cast(Awaitable[int], redis.delete(key, index_key, tail_key))
    return result


@handle_redis_operation(default_return=None,
                        log_operation="removing player from answer queue")
async def remove_from_answer_queue(redis: Redis, room_id: str,
                                   player_id: int) -> int | None:
    """
    Remove a specific player from the answer queue in Redis sorted set.

    Args:
        redis: Redis连接
        room_id (str): The room identifier
        player_id (int): The player ID to remove

    Returns:
        int or None: Number of members removed, or None if error
    """
    key = RedisKeys.answer_queue(room_id)
    index_key = RedisKeys.answer_queue_player_index(room_id)
    tail_key = RedisKeys.answer_queue_tail_player(room_id)
    player_id_key = str(player_id)

    # 优先通过索引删除，避免遍历 + JSON 反序列化
    member_json = await cast(Awaitable[str | None],
                             redis.hget(index_key, player_id_key))
    if member_json:
        removed = await cast(Awaitable[int], redis.zrem(key, member_json))
        await cast(Awaitable[int], redis.hdel(index_key, player_id_key))
        current_tail = await cast(Awaitable[str | None], redis.get(tail_key))
        if current_tail is not None and current_tail == player_id_key:
            await cast(Awaitable[int], redis.delete(tail_key))
        return removed

    # 兼容历史数据：索引缺失时回退扫描，并补建索引
    members = await cast(Awaitable[list[str]], redis.zrange(key, 0, -1))
    removed_count = 0

    for raw_member in members:
        try:
            item = AnswerQueueItem.model_validate_json(raw_member, strict=False)
            await cast(
                Awaitable[int],
                redis.hsetnx(index_key, str(item.player_id), raw_member),
            )
            if item.player_id == player_id:
                result = await cast(Awaitable[int], redis.zrem(key, raw_member))
                await cast(Awaitable[int], redis.hdel(index_key, player_id_key))
                current_tail = await cast(Awaitable[str | None], redis.get(tail_key))
                if current_tail is not None and current_tail == player_id_key:
                    await cast(Awaitable[int], redis.delete(tail_key))
                if result:
                    removed_count += 1
        except (ValidationError, ValueError) as e:
            logger.warning("Failed to parse answer queue member: %s", e)
            continue

    return removed_count


@handle_redis_operation(default_return=None, log_operation="getting current answerer")
async def get_room_current_answerer(redis: Redis, room_id: str) -> int | None:
    """获取当前答题者"""
    key = RedisKeys.answerer(room_id)
    answerer = await cast(Awaitable[int | None], redis.get(key))
    return answerer


@handle_redis_operation(default_return=None,
                        log_operation="getting answer queue tail player")
async def get_answer_queue_tail_player_id(redis: Redis, room_id: str) -> int | None:
    """获取当前回合抢答队列边界（已处理段的队尾玩家 ID）。"""
    key = RedisKeys.answer_queue_tail_player(room_id)
    raw_tail_player = await cast(Awaitable[str | None], redis.get(key))
    if raw_tail_player is None:
        return None
    try:
        return int(raw_tail_player)
    except (TypeError, ValueError):
        return None


@handle_redis_operation(default_return=False,
                        log_operation="setting answer queue tail player")
async def set_answer_queue_tail_player_id(redis: Redis, room_id: str,
                                          player_id: int) -> bool:
    """设置当前回合抢答队列边界（已处理段的队尾玩家 ID）。"""
    key = RedisKeys.answer_queue_tail_player(room_id)
    await cast(Awaitable, redis.set(key, player_id, ex=ROOM_TTL_SECONDS))
    return True


@handle_redis_operation(default_return=False,
                        log_operation="clearing answer queue tail player")
async def clear_answer_queue_tail_player_id(redis: Redis, room_id: str) -> bool:
    """清除当前回合抢答队列边界。"""
    key = RedisKeys.answer_queue_tail_player(room_id)
    await cast(Awaitable, redis.delete(key))
    return True


@handle_redis_operation(default_return=False, log_operation="setting current answerer")
async def set_room_current_answerer(redis: Redis, room_id: str, player_id: int) -> bool:
    """设置当前答题者"""
    key = RedisKeys.answerer(room_id)
    await cast(Awaitable, redis.set(key, player_id, ex=ROOM_TTL_SECONDS))
    return True


# Lua script: atomically check current answerer and set if empty or same player.
# Returns 1 if set succeeded, 0 if another player is already the answerer.
_TRY_SET_ANSWERER_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current == false or current == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    return 1
end
return 0
"""


@handle_redis_operation(default_return=False,
                        log_operation="atomically setting current answerer")
async def try_set_room_current_answerer(redis: Redis, room_id: str,
                                        player_id: int) -> bool:
    """Atomically set current answerer only if nobody else is already answering.

    Uses a Lua script to make the GET + conditional SET a single atomic
    operation, preventing the TOCTOU race where multiple concurrent
    ATTEMPT_ANSWER handlers all observe ``current_answerer is None`` and
    each enter the "first answerer" code-path.

    Args:
        redis: Redis connection
        room_id: The room identifier
        player_id: The player to set as current answerer

    Returns:
        True if the answerer was set (either was empty or already same
        player), False if someone else is already the answerer.
    """
    key = RedisKeys.answerer(room_id)
    expected = str(player_id)
    result = await cast(
        Awaitable[int],
        redis.eval(_TRY_SET_ANSWERER_SCRIPT, 1, key, expected, expected,
                   ROOM_TTL_SECONDS),
    )
    return bool(result)


# Lua script: atomically check current answerer == from_player, then
# transition to to_player (or clear if to_player is empty string).
# Returns 1 if transition succeeded, 0 if current answerer doesn't match.
_TRANSITION_ANSWERER_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current ~= ARGV[1] then
    return 0
end
if ARGV[2] == '' then
    redis.call('DEL', KEYS[1])
else
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
end
return 1
"""


@handle_redis_operation(default_return=False,
                        log_operation="transitioning current answerer")
async def transition_room_current_answerer(
    redis: Redis,
    room_id: str,
    from_player_id: int,
    to_player_id: int | None,
) -> bool:
    """Atomically transition current answerer from one player to another.

    Uses a Lua script to make the check-and-set atomic, preventing the
    TOCTOU race where concurrent SUBMIT_ANSWER handlers both read the
    same queue and attempt to set the next player.

    Args:
        redis: Redis connection
        room_id: The room identifier
        from_player_id: The expected current answerer (must match)
        to_player_id: The next answerer, or None to clear the answerer

    Returns:
        True if the transition succeeded, False if current answerer
        doesn't match ``from_player_id``.
    """
    key = RedisKeys.answerer(room_id)
    from_id = str(from_player_id)
    to_id = str(to_player_id) if to_player_id is not None else ""
    result = await cast(
        Awaitable[int],
        redis.eval(_TRANSITION_ANSWERER_SCRIPT, 1, key, from_id, to_id,
                   ROOM_TTL_SECONDS),
    )
    return bool(result)


async def delete_all_room_cache(room_id: str) -> None:
    """删除房间所有 Redis 缓存（用于房间解散时清理）

    Args:
        room_id (str): 房间 ID
    """
    try:
        redis = await get_redis()
        # 删除播放状态
        await delete_room_playback_state(redis, room_id)
        # 清空答题队列
        await clear_answer_queue(redis, room_id)
        # 清空当前答题者
        await clear_room_current_answerer(redis, room_id)
        logger.info("Deleted all Redis cache for room %s", room_id)
    except Exception as e:
        logger.error("Error deleting all room cache for room %s: %s", room_id, e)
        raise


@handle_redis_operation(default_return=False,
                        log_operation="syncing answer queue is_answering")
async def sync_answer_queue_is_answering(redis: Redis, room_id: str,
                                         player_id: int) -> bool:
    """同步答题队列中 is_answering 字段，确保仅当前答题者为 True。"""
    key = RedisKeys.answer_queue(room_id)
    index_key = RedisKeys.answer_queue_player_index(room_id)

    members_with_scores = await cast(Awaitable[list[tuple[str, float]]],
                                     redis.zrange(key, 0, -1, withscores=True))
    if not members_with_scores:
        return False

    changed = False
    for raw_member, score in members_with_scores:
        try:
            item = AnswerQueueItem.model_validate_json(raw_member, strict=False)
        except (ValidationError, ValueError) as e:
            logger.warning("Failed to parse answer queue member: %s", e)
            continue

        should_answering = item.player_id == player_id
        if item.is_answering == should_answering:
            # 顺便修复/补建索引
            await cast(
                Awaitable,
                redis.hset(index_key, str(item.player_id), raw_member),
            )
            continue

        updated_item = item.model_copy(update={"is_answering": should_answering})
        updated_member = updated_item.model_dump_json()

        await cast(Awaitable[int], redis.zrem(key, raw_member))
        await cast(Awaitable[int], redis.zadd(key, {updated_member: score}))
        await cast(Awaitable, redis.hset(index_key, str(item.player_id),
                                         updated_member))
        changed = True

    await cast(Awaitable[bool], redis.expire(key, ROOM_TTL_SECONDS))
    await cast(Awaitable[bool], redis.expire(index_key, ROOM_TTL_SECONDS))
    return changed


@handle_redis_operation(default_return=False, log_operation="clearing current answerer")
async def clear_room_current_answerer(redis: Redis, room_id: str) -> bool:
    """清除当前答题者"""
    key = RedisKeys.answerer(room_id)
    await cast(Awaitable, redis.delete(key))
    return True


async def get_room_play_progress(room_id: str) -> int:
    """获取房间播放进度（毫秒）——从 playback_state Redis 键读取。"""
    state = await get_room_playback_state(room_id)
    return state.progress_ms if state else 0


async def update_room_playback_state(
    room_id: str,
    progress_ms: int,
    offset_ts: int,
    audio_url: str | None,
) -> bool:
    """更新房间播放状态（仅写 playback Redis 键，round_state 已由 SQL 管理）。"""
    existing = await get_room_playback_state(room_id)
    if existing is None:
        logger.warning("update_room_playback_state: no playback state for room %s",
                       room_id)
        return False
    updated = existing.model_copy(
        update={
            "progress_ms": progress_ms,
            "offset_ts": offset_ts,
            **({
                "audio_url": audio_url
            } if audio_url is not None else {}),
        })
    await set_room_playback_state(room_id, updated)
    return True
