from .connection import get_redis
from redis.asyncio.client import Redis
from .schemas import *
from .utils import RedisKeys, ROOM_TTL_SECONDS
from typing import Optional, cast, Awaitable
from utils import get_logger

logger = get_logger(__name__)


# song queue 变化不会很大，所以没必要 Redis，直接扔数据库
async def load_room_state(room_id: str) -> RoomBaseStateCache | None:
    """从 Redis 获取房间状态

    Args:
        room_id (str): 房间 ID

    Returns:
        RoomBaseStateCache | None: 房间状态缓存对象，如果未找到则返回 None
    """
    redis: Redis = await get_redis()

    try:
        key = RedisKeys.room(room_id)
        fields = await cast(Awaitable[dict], redis.hgetall(key))
        if not fields:
            logger.debug(f"No room state found for room {room_id}")
            return None

        # 从 Redis Hash 重建模型
        state = RoomBaseStateCache.from_redis_hash(fields)
        logger.debug(f"Loaded room state for room {room_id}")
        return state
    except Exception as e:
        logger.error(f"Error loading room state for room {room_id}: {e}")
        return None


async def save_room_state(room_id: str, state: RoomBaseStateCache):
    """将房间状态保存到 Redis

    Args:
        room_id (str): 房间 ID
        state (RoomBaseStateCache): 房间状态缓存对象
    """
    redis: Redis = await get_redis()

    try:
        key = RedisKeys.room(room_id)
        # 将模型转换为 Redis Hash 映射
        mapping = state.to_redis_hash()
        await cast(Awaitable, redis.hset(key, mapping=mapping))
        # 设置过期时间（6小时）
        await cast(Awaitable, redis.expire(key, ROOM_TTL_SECONDS))
        logger.debug(f"Saved room state for room {room_id}")
    except Exception as e:
        logger.error(f"Error saving room state for room {room_id}: {e}")


async def set_room_playback_state(room_id: str, playback_state: PlaybackState):
    """将房间播放状态保存到 Redis

    Args:
        room_id (str): 房间 ID
        playback_state (PlaybackState): 播放状态对象
    """
    redis = await get_redis()

    try:
        key = RedisKeys.playback_state(room_id)
        await cast(Awaitable,
                   redis.hset(key, mapping=playback_state.to_redis_hash()))
        await cast(Awaitable, redis.expire(key, ROOM_TTL_SECONDS))
        logger.debug(f"Saved playback state for room {room_id}")
    except Exception as e:
        logger.error(f"Error saving playback state for room {room_id}: {e}")


async def set_room_playback_progress(room_id: str, progress_ms: int,
                                     offset_ts: int):
    """更新房间播放进度（仅 progress_ms 和 offset_ts）

    Args:
        room_id (str): 房间 ID
        progress_ms (int): 当前播放进度（毫秒）
        offset_ts (int): 服务器时间戳（毫秒）
    """
    redis = await get_redis()

    try:
        key = RedisKeys.playback_state(room_id)
        await cast(
            Awaitable,
            redis.hset(key,
                       mapping={
                           "progress_ms": str(progress_ms),
                           "offset_ts": str(offset_ts),
                       }))
        logger.debug(
            f"Updated playback progress for room {room_id}: progress_ms={progress_ms}, offset_ts={offset_ts}"
        )
    except Exception as e:
        logger.error(
            f"Error updating playback progress for room {room_id}: {e}")


async def get_room_playback_state(room_id: str) -> PlaybackState | None:
    """获取房间播放状态

    Args:
        room_id (str): 房间 ID

    Returns:
        PlaybackState | None: 播放状态对象，如果未找到则返回 None
    """
    redis = await get_redis()

    try:
        key = RedisKeys.playback_state(room_id)
        data = await cast(Awaitable[dict], redis.hgetall(key))
        if not data:
            logger.debug(f"No playback state found for room {room_id}")
            return None

        # 从 Redis Hash 重建模型
        playback_state = PlaybackState.from_redis_hash(data)
        logger.debug(f"Loaded playback state for room {room_id}")
        return playback_state
    except Exception as e:
        logger.error(f"Error loading playback state for room {room_id}: {e}")
        return None


async def delete_room_playback_state(room_id: str):
    """删除房间播放状态

    Args:
        room_id (str): 房间 ID
    """
    redis = await get_redis()

    try:
        key = RedisKeys.playback_state(room_id)
        await cast(Awaitable, redis.delete(key))
        logger.debug(f"Deleted playback state for room {room_id}")
    except Exception as e:
        logger.error(f"Error deleting playback state for room {room_id}: {e}")


async def save_room_players(room_id: str, players: list[RoomStatePlayerItem]):
    redis = await get_redis()

    try:
        key = RedisKeys.room_players(room_id)
        # player_ids 需要转换为字符串
        player_ids = [str(p.id) for p in players]
        async with redis.pipeline(transaction=True) as pipe:
            await cast(Awaitable, pipe.sadd(key, *player_ids))
            for player in players:
                player_key = RedisKeys.room_player_status(
                    room_id, str(player.id))
                await cast(
                    Awaitable,
                    pipe.hset(player_key, mapping=player.to_redis_hash()))
            result = await pipe.execute()
            logger.debug(
                f"Saved room players for room {room_id}, result: {result}")
        return result
    except Exception as e:
        logger.error(f"Error saving room players for room {room_id}: {e}")
        return None


async def get_room_players(room_id: str) -> list[RoomStatePlayerItem]:
    redis = await get_redis()

    try:
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
                    except Exception as e:
                        logger.error(
                            f"Error parsing player data from Redis: {e}")
        logger.debug(f"Retrieved {len(players)} players for room {room_id}")
        return players
    except Exception as e:
        logger.error(f"Error getting room players for room {room_id}: {e}")
        return []


async def delete_room_players(room_id: str):
    redis = await get_redis()

    try:
        key = RedisKeys.room_players(room_id)
        player_ids = await cast(Awaitable[set], redis.smembers(key))
        async with redis.pipeline(transaction=True) as pipe:
            await cast(Awaitable, pipe.delete(key))
            for pid in player_ids:
                player_key = RedisKeys.room_player_status(room_id, pid)
                await cast(Awaitable, pipe.delete(player_key))
            result = await pipe.execute()
            logger.debug(
                f"Deleted room players for room {room_id}, result: {result}")
        return result
    except Exception as e:
        logger.error(f"Error deleting room players for room {room_id}: {e}")
        return None


async def set_room_player(room_id: str, player: RoomStatePlayerItem):
    redis = await get_redis()

    try:
        key = RedisKeys.room_player_status(room_id, str(player.id))
        result = await cast(Awaitable,
                            redis.hset(key, mapping=player.to_redis_hash()))
        logger.debug(
            f"Set room player {player.id} for room {room_id}, result: {result}"
        )
        return result
    except Exception as e:
        logger.error(f"Error setting room player for room {room_id}: {e}")
        return None


async def get_room_player(room_id: str,
                          player_id: int) -> Optional[RoomStatePlayerItem]:
    redis = await get_redis()

    try:
        key = RedisKeys.room_player_status(room_id, str(player_id))
        data = await cast(Awaitable[dict], redis.hgetall(key))
        if not data:
            logger.debug(f"No player {player_id} found for room {room_id}")
            return None
        player = RoomStatePlayerItem.from_redis_hash(data)
        logger.debug(
            f"Retrieved player {player_id} for room {room_id}: {player}")
        return player
    except Exception as e:
        logger.error(f"Error getting room player for room {room_id}: {e}")
        return None


async def update_room_player_online_status(room_id: str, player_id: int,
                                           online: bool):
    redis = await get_redis()

    try:
        key = RedisKeys.room_player_status(room_id, str(player_id))
        result = await cast(Awaitable, redis.hset(key, "online", str(online)))
        logger.debug(
            f"Updated online status for player {player_id} in room {room_id} to {online}, result: {result}"
        )
        return result
    except Exception as e:
        logger.error(
            f"Error updating player online status for room {room_id}: {e}")
        return None


async def remove_room_player(room_id: str, player_id: int):
    """
    从房间缓存中移除指定玩家
    """
    redis = await redis_client.get_client()
    if not redis:
        return
    room_key = f"room:{room_id}:players"
    players = await redis.get(room_key)
    if players:
        players = json.loads(players)
        # 过滤掉要移除的玩家
        players = [p for p in players if p['id'] != player_id]
        await redis.set(room_key, json.dumps(players))


# 以下这三个方法是比较核心的，涉及答题队列的维护（利用 Redis 有序集合的特性）
async def append_attempt_answer_player(room_id: str, data: AnswerQueueItem):
    """
    Asynchronously appends a player's answer attempt to the answer queue for a given room in Redis.

    Each player's attempt is serialized as a JSON string and added to a sorted set, where the score is calculated as
    `server_ts * 0.001 + offset_ts`. This scoring ensures unique ordering and high time precision, preventing conflicts
    when multiple players submit answers within the same millisecond.

    Args:
        room_id (str): The unique identifier of the room.
        data (AnswerQueueItem): The player's answer attempt data, including timestamps and player information.

    Returns:
        int or None: The result of the Redis ZADD operation (number of elements added), or None if an error occurs.

    Raises:
        Exception: Logs and suppresses any exceptions that occur during the Redis operation.
    """
    redis = await get_redis()
    key = RedisKeys.answer_queue(room_id)
    try:
        member_key = data.model_dump_json()  # 将整个对象序列化为 JSON 字符串作为成员值
        # 将玩家添加到答题队列（有序集合），score 为 server_ts * 0.0001 + offset_ts
        # 即以 offset_ts 为主，server_ts 作为微调，确保同一毫秒内的玩家顺序由服务器时间决定
        # 这样可以保证队列顺序的唯一性和时间精度，防止同一毫秒内多玩家并发导致顺序冲突
        # First, check if player already exists in queue
        entries = await cast(Awaitable[list], redis.zrange(key, 0, -1))
        key_player_prefix = '{"player_id":' + str(data.player_id) + ','
        logger.debug(
            f"Current answer queue keys for room {room_id}: {entries}, checking for player_id {data.player_id} with prefix {key_player_prefix}"
        )
        if any(k.startswith(key_player_prefix) for k in entries):
            logger.warning(
                f"Player {data.player_id} is already in the answer queue for room {room_id}, skipping append"
            )
            raise ValueError(
                f"Player {data.player_id} is already in the answer queue")

        # Then add to queue
        result = await cast(
            Awaitable,
            redis.zadd(key,
                       {member_key: data.server_ts * 0.0001 + data.offset_ts}))
        logger.debug(
            f"Appended player {data.player_id} to answer queue for room {room_id}, result: {result}"
        )
        return result
    except ValueError as ve:
        raise ve  # 业务逻辑错误，抛出给调用方处理
    except Exception as e:
        logger.error(
            f"Error appending player to answer queue for room {room_id}: {e}",
            exc_info=True)
        return None


async def get_answer_queue(room_id: str) -> list[RoomSchemas.AnswerQueueItem]:
    """
    Retrieve the answer queue for a given room from Redis.

    Args:
        room_id (str): The unique identifier of the room.

    Returns:
        list[AnswerQueueItem]: A list of AnswerQueueItem objects representing the players in the answer queue,
        ordered by their offset timestamp (score in the sorted set). If an error occurs, returns an empty list.

    Raises:
        None: All exceptions are caught and logged internally.
    """
    redis = await get_redis()
    key = RedisKeys.answer_queue(room_id)
    try:
        # 获取有序集合中的所有玩家ID（offset_ts）
        entries = await cast(Awaitable[list], redis.zrange(key, 0, -1))
        answer_queue = [
            RoomSchemas.AnswerQueueItem.model_validate_json(
                p, strict=False).model_copy(update={"order": o + 1})
            for o, p in enumerate(entries)
        ]
        logger.debug(
            f"Retrieved answer queue for room {room_id}: {answer_queue}")
        return answer_queue
    except Exception as e:
        logger.error(f"Error getting answer queue for room {room_id}: {e}")
        return []


async def clear_answer_queue(room_id: str):
    """
    Asynchronously clears the answer queue for a given room in Redis.

    Args:
        room_id (str): The unique identifier of the room whose answer queue should be cleared.

    Returns:
        int or None: The number of keys that were removed from Redis, or None if an error occurred.

    Logs:
        - Debug message upon successful deletion.
        - Error message if an exception occurs during deletion.
    """
    redis = await get_redis()
    key = RedisKeys.answer_queue(room_id)
    try:
        result = await cast(Awaitable, redis.delete(key))
        logger.debug(
            f"Cleared answer queue for room {room_id}, result: {result}")
        return result
    except Exception as e:
        logger.error(f"Error clearing answer queue for room {room_id}: {e}")
        return None


async def remove_from_answer_queue(room_id: str, player_id: int):
    """
    Remove a specific player from the answer queue in Redis sorted set.

    Args:
        room_id (str): The room identifier
        player_id (int): The player ID to remove

    Returns:
        int or None: Number of members removed, or None if error
    """
    redis = await get_redis()
    key = RedisKeys.answer_queue(room_id)
    try:
        # Get all members in the sorted set
        members = await cast(Awaitable[list], redis.zrange(key, 0, -1))
        removed_count = 0

        for member_json in members:
            try:
                # Parse the JSON to check player_id
                item = AnswerQueueItem.model_validate_json(member_json,
                                                           strict=False)
                if item.player_id == player_id:
                    # Remove this member
                    result = await cast(Awaitable,
                                        redis.zrem(key, member_json))
                    if result:
                        removed_count += 1
                        logger.debug(
                            f"Removed player {player_id} from answer queue in room {room_id}"
                        )
            except Exception as e:
                logger.warning(f"Failed to parse answer queue member: {e}")
                continue

        return removed_count
    except Exception as e:
        logger.error(
            f"Error removing player from answer queue for room {room_id}: {e}")
        return None
