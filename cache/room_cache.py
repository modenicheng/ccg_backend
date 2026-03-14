"""Redis cache module for playback and answer-queue state."""

from __future__ import annotations

from functools import update_wrapper
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar, cast, Concatenate

from pydantic import ValidationError
from redis.asyncio.client import Redis

from db.models import RoomStatusORM
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
                       Awaitable[R]]) -> Callable[P, Awaitable[R]]:

        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
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


@handle_redis_operation(default_return=None, log_operation="saving playback state")
async def set_room_playback_state(redis: Redis, room_id: str,
                                  playback_state: PlaybackState) -> None:
    """将房间播放状态保存到 Redis

    Args:
        redis: Redis连接
        room_id (str): 房间 ID
        playback_state (PlaybackState): 播放状态对象
    """
    key = RedisKeys.playback_state(room_id)
    await cast(Awaitable, redis.hset(key, mapping=playback_state.to_redis_hash()))
    await cast(Awaitable, redis.expire(key, ROOM_TTL_SECONDS))


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
    if not data:
        logger.debug("No playback state found for room %s", room_id)
        return None

    # 从 Redis Hash 重建模型
    playback_state = PlaybackState.from_redis_hash(data)
    return playback_state


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
@handle_redis_operation(default_return=None,
                        log_operation="appending player to answer queue",
                        reraise_exceptions=(ValueError,))
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
            data.player_id, room_id)
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
    result = await cast(Awaitable[int], redis.delete(key, index_key))
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
    player_id_key = str(player_id)

    # 优先通过索引删除，避免遍历 + JSON 反序列化
    member_json = await cast(Awaitable[str | None],
                             redis.hget(index_key, player_id_key))
    if member_json:
        removed = await cast(Awaitable[int], redis.zrem(key, member_json))
        await cast(Awaitable[int], redis.hdel(index_key, player_id_key))
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


@handle_redis_operation(default_return=False, log_operation="setting current answerer")
async def set_room_current_answerer(redis: Redis, room_id: str, player_id: int) -> bool:
    """设置当前答题者"""
    key = RedisKeys.answerer(room_id)
    await cast(Awaitable, redis.set(key, player_id, ex=ROOM_TTL_SECONDS))
    return True


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
        await cast(Awaitable, redis.hset(index_key, str(item.player_id), updated_member))
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
