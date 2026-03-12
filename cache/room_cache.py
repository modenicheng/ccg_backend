"""Room state caching module using Redis."""

from __future__ import annotations

from functools import update_wrapper
from typing import Any, Awaitable, Callable, Optional, ParamSpec, TypeVar, cast, Concatenate

from pydantic import ValidationError
from redis.asyncio.client import Redis

from db.models import RoomStatusORM
from schemas.ws_messages import room_schemas as RoomSchemas
from utils import get_logger

from .connection import get_redis
from .schemas import (
    AnswerQueueItem,
    PlaybackState,
    RoomBaseStateCache,
    RoomStatePlayerItem,
)
from .utils import RedisKeys, ROOM_TTL_SECONDS

logger = get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def handle_redis_operation(
    default_return: Any = None,
    log_operation: str = "",
    reraise_exceptions: tuple[type[Exception], ...] = (),
) -> Callable[[Callable[Concatenate[Redis, P], Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    处理Redis操作通用逻辑的装饰器

    被装饰的函数应该接受redis连接作为第一个参数，其他参数保持不变。
    装饰器会自动获取redis连接，处理异常，并记录日志。

    Args:
        default_return: 发生异常时返回的默认值
        log_operation: 操作描述，用于日志（如"loading room state"）
        reraise_exceptions: 需要重新抛出的异常类型元组
    """

    def decorator(func: Callable[Concatenate[Redis, P], Awaitable[R]]) -> Callable[P, Awaitable[R]]:

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
    """Normalize legacy room status values to canonical numeric string."""
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


# song queue 变化不会很大，所以没必要 Redis，直接扔数据库
@handle_redis_operation(default_return=None)
async def load_room_state(redis: Redis, room_id: str) -> RoomBaseStateCache | None:
    """从 Redis 获取房间状态

    Args:
        redis: Redis连接
        room_id (str): 房间 ID

    Returns:
        RoomBaseStateCache | None: 房间状态缓存对象，如果未找到则返回 None
    """
    key = RedisKeys.room(room_id)
    fields = await cast(Awaitable[dict], redis.hgetall(key))
    if not fields:
        logger.debug("No room state found for room %s", room_id)
        return None

    try:
        # 从 Redis Hash 重建模型
        state = RoomBaseStateCache.from_redis_hash(fields)
        return state
    except ValidationError:
        # 兼容历史 status 写法（如 ROOMSTATUS.RUNNING / RUNNING 等）
        status_value = fields.get("status")
        normalized_status = _normalize_status_value(status_value)
        if normalized_status is not None:
            try:
                fields["status"] = normalized_status
                state = RoomBaseStateCache.from_redis_hash(fields)
                await cast(Awaitable, redis.hset(key, "status", normalized_status))
                await cast(Awaitable, redis.expire(key, ROOM_TTL_SECONDS))
                logger.warning(
                    "Migrated legacy room status for room %s: %s -> %s",
                    room_id,
                    status_value,
                    normalized_status,
                )
                return state
            except Exception as migrate_err:  # pylint: disable=broad-exception-caught
                logger.error(
                    "Failed to migrate room status for room %s: %s",
                    room_id,
                    migrate_err,
                )
        # 验证错误无法恢复，返回None
        return None


@handle_redis_operation(default_return=None, log_operation="saving room state")
async def save_room_state(redis: Redis, room_id: str,
                          state: RoomBaseStateCache) -> None:
    """将房间状态保存到 Redis

    Args:
        redis: Redis连接
        room_id (str): 房间 ID
        state (RoomBaseStateCache): 房间状态缓存对象
    """
    key = RedisKeys.room(room_id)
    # 将模型转换为 Redis Hash 映射
    mapping = state.to_redis_hash()
    await cast(Awaitable, redis.hset(key, mapping=mapping))
    # 设置过期时间（6小时）
    await cast(Awaitable, redis.expire(key, ROOM_TTL_SECONDS))


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


@handle_redis_operation(default_return=None, log_operation="saving room players")
async def save_room_players(redis: Redis, room_id: str,
                            players: list[RoomStatePlayerItem]) -> list | None:
    key = RedisKeys.room_players(room_id)
    # player_ids 需要转换为字符串
    player_ids = [str(p.id) for p in players]
    async with redis.pipeline(transaction=True) as pipe:
        await cast(Awaitable, pipe.sadd(key, *player_ids))
        for player in players:
            player_key = RedisKeys.room_player_status(room_id, str(player.id))
            await cast(Awaitable, pipe.hset(player_key, mapping=player.to_redis_hash()))
        result = await pipe.execute()
    return result


@handle_redis_operation(default_return=[], log_operation="getting room players")
async def get_room_players(redis: Redis, room_id: str) -> list[RoomStatePlayerItem]:
    key = RedisKeys.room_players(room_id)
    player_ids_str = await cast(Awaitable[set], redis.smembers(key))
    players: list[RoomStatePlayerItem] = []
    async with redis.pipeline(transaction=True) as pipe:
        for pid_str in player_ids_str:
            player_key = RedisKeys.room_player_status(room_id, pid_str)
            await cast(Awaitable, pipe.hgetall(player_key))
        results = await pipe.execute()
        for data in results:
            if data:
                try:
                    player = RoomStatePlayerItem.from_redis_hash(data)
                    players.append(player)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.error("Error parsing player data from Redis: %s",
                                 e,
                                 exc_info=True)
    return players


@handle_redis_operation(default_return=None, log_operation="deleting room players")
async def delete_room_players(redis: Redis, room_id: str) -> list | None:
    key = RedisKeys.room_players(room_id)
    player_ids = await cast(Awaitable[set], redis.smembers(key))
    async with redis.pipeline(transaction=True) as pipe:
        await cast(Awaitable, pipe.delete(key))
        for pid in player_ids:
            player_key = RedisKeys.room_player_status(room_id, pid)
            await cast(Awaitable, pipe.delete(player_key))
        result = await pipe.execute()
    return result


@handle_redis_operation(default_return=None, log_operation="setting room player")
async def set_room_player(redis: Redis, room_id: str,
                          player: RoomStatePlayerItem) -> int | None:
    key = RedisKeys.room_player_status(room_id, str(player.id))
    result = await cast(Awaitable, redis.hset(key, mapping=player.to_redis_hash()))
    return result


@handle_redis_operation(default_return=None, log_operation="getting room player")
async def get_room_player(redis: Redis, room_id: str,
                          player_id: int) -> Optional[RoomStatePlayerItem]:
    key = RedisKeys.room_player_status(room_id, str(player_id))
    data = await cast(Awaitable[dict], redis.hgetall(key))
    if not data:
        logger.debug("No player %s found for room %s", player_id, room_id)
        return None
    player = RoomStatePlayerItem.from_redis_hash(data)
    return player


@handle_redis_operation(default_return=None,
                        log_operation="updating player online status")
async def update_room_player_online_status(redis: Redis, room_id: str, player_id: int,
                                           online: bool) -> int | None:
    key = RedisKeys.room_player_status(room_id, str(player_id))
    result = await cast(Awaitable, redis.hset(key, "online", str(online)))
    return result


@handle_redis_operation(default_return=None, log_operation="removing room player")
async def remove_room_player(redis: Redis, room_id: str, player_id: int) -> None:
    """
    从房间缓存中移除指定玩家

    从玩家集合中移除玩家ID，并删除对应的玩家状态Hash。
    """
    # 从玩家集合中移除玩家ID
    players_key = RedisKeys.room_players(room_id)
    await cast(Awaitable, redis.srem(players_key, str(player_id)))

    # 删除玩家状态Hash
    player_status_key = RedisKeys.room_player_status(room_id, str(player_id))
    await cast(Awaitable, redis.delete(player_status_key))


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
    ordering and high time precision, preventing conflicts when multiple players submit answers within
    the same millisecond.

    Args:
        redis: Redis connection
        room_id (str): The unique identifier of the room.
        data (AnswerQueueItem): The player's answer attempt data, including timestamps and player information.

    Returns:
        int or None: The result of the Redis ZADD operation (number of elements added), or None if an error occurs.

    Raises:
        ValueError: If the player is already in the answer queue (business logic error).
    """
    key = RedisKeys.answer_queue(room_id)
    member_key = data.model_dump_json()  # 将整个对象序列化为 JSON 字符串作为成员值
    # 将玩家添加到答题队列（有序集合），score 为 server_ts * 0.0001 + offset_ts
    # 即以 offset_ts 为主，server_ts 作为微调，确保同一毫秒内的玩家顺序由服务器时间决定
    # 这样可以保证队列顺序的唯一性和时间精度，防止同一毫秒内多玩家并发导致顺序冲突
    # First, check if player already exists in queue
    entries = await cast(Awaitable[list], redis.zrange(key, 0, -1))
    key_player_prefix = '{"player_id":' + str(data.player_id) + ","
    if any(k.startswith(key_player_prefix) for k in entries):
        logger.warning(
            "Player %s is already in the answer queue for room %s, skipping append",
            data.player_id, room_id)
        raise ValueError(f"Player {data.player_id} is already in the answer queue")

    # Then add to queue
    result = await cast(
        Awaitable,
        redis.zadd(key, {member_key: data.server_ts * 0.0001 + data.offset_ts}),
    )
    return result


@handle_redis_operation(default_return=[], log_operation="getting answer queue")
async def get_answer_queue(redis: Redis,
                           room_id: str) -> list[RoomSchemas.AnswerQueueItem]:
    """
    Retrieve the answer queue for a given room from Redis.

    Args:
        redis: Redis connection
        room_id (str): The unique identifier of the room.

    Returns:
        list[AnswerQueueItem]: A list of AnswerQueueItem objects representing the players in the answer queue,
        ordered by their offset timestamp (score in the sorted set). If an error occurs, returns an empty list.
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
    result = await cast(Awaitable, redis.delete(key))
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
    # Get all members in the sorted set
    members = await cast(Awaitable[list], redis.zrange(key, 0, -1))
    removed_count = 0

    for member_json in members:
        try:
            # Parse the JSON to check player_id
            item = AnswerQueueItem.model_validate_json(member_json, strict=False)
            if item.player_id == player_id:
                # Remove this member
                result = await cast(Awaitable, redis.zrem(key, member_json))
                if result:
                    removed_count += 1
        except ValidationError as e:
            logger.warning("Failed to parse answer queue member: %s", e)
            continue

    return removed_count


@handle_redis_operation(default_return=None, log_operation="getting current answerer")
async def get_room_current_answerer(redis: Redis, room_id: str) -> str | None:
    """获取当前答题者"""
    key = RedisKeys.room(room_id)
    answerer = await cast(Awaitable[Optional[bytes]],
                          redis.hget(key, "current_answerer"))
    return answerer.decode() if answerer else None


@handle_redis_operation(default_return=False, log_operation="setting current answerer")
async def set_room_current_answerer(redis: Redis, room_id: str, player_id: str) -> bool:
    """设置当前答题者"""
    key = RedisKeys.room(room_id)
    await cast(Awaitable, redis.hset(key, "current_answerer", player_id))
    await cast(Awaitable, redis.expire(key, ROOM_TTL_SECONDS))
    # 成功执行到这里表示没有异常，返回True
    return True


@handle_redis_operation(default_return=0, log_operation="getting play progress")
async def get_room_play_progress(redis: Redis, room_id: str) -> int:
    """获取房间播放进度（毫秒）"""
    key = RedisKeys.room(room_id)
    progress = await cast(Awaitable[Optional[bytes]], redis.hget(key, "play_progress"))
    return int(progress) if progress else 0


@handle_redis_operation(default_return=False, log_operation="updating playback state")
async def update_room_playback_state(
    redis: Redis,
    room_id: str,
    round_state: str,
    progress_ms: int,
    offset_ts: int,
    audio_url: str | None,
    event_ts: int,
    event_name: str,
) -> bool:
    """更新房间播放状态（内部字段）"""
    key = RedisKeys.room(room_id)
    mapping = {
        "current_round_state": round_state,
        "play_progress": str(progress_ms),
        "play_offset_ts": str(offset_ts),
        "last_control_ts": str(event_ts),
        "last_control_event": event_name,
    }
    if audio_url is not None:
        mapping["audio_url"] = audio_url
    await cast(Awaitable, redis.hset(key, mapping=mapping))
    await cast(Awaitable, redis.expire(key, ROOM_TTL_SECONDS))
    # 成功执行到这里表示没有异常，返回True
    return True


@handle_redis_operation(default_return=False, log_operation="updating round state")
async def update_room_round_state(
    redis: Redis,
    room_id: str,
    round_state: str,
    event_ts: int,
    event_name: str,
) -> bool:
    """仅更新回合状态相关字段，不覆盖播放进度。"""
    key = RedisKeys.room(room_id)
    mapping = {
        "current_round_state": round_state,
        "last_control_ts": str(event_ts),
        "last_control_event": event_name,
    }
    await cast(Awaitable, redis.hset(key, mapping=mapping))
    await cast(Awaitable, redis.expire(key, ROOM_TTL_SECONDS))
    # 成功执行到这里表示没有异常，返回True
    return True
