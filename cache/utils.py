from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Optional, cast, Awaitable

from pydantic import BaseModel

from .connection import get_redis
from .schemas import *

ROOM_TTL_SECONDS = 6 * 60 * 60


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RedisKeys:
    """Redis 键名生成类"""

    @staticmethod
    def room(room_id: str) -> str:
        return f"room:{room_id}"

    @staticmethod
    def room_players(room_id: str) -> str:
        return f"room:{room_id}:players"

    @staticmethod
    def room_player_status(room_id: str, player_id: str) -> str:
        return f"room:{room_id}:player:{player_id}"

    @staticmethod
    def room_song_queue(room_id: str) -> str:
        return f"room:{room_id}:song_queue"

    @staticmethod
    def room_online(room_id: str) -> str:
        return f"room:{room_id}:online"

    @staticmethod
    def playback_state(room_id: str) -> str:
        return f"room:{room_id}:playback"
    
    @staticmethod
    def answer_queue(room_id: str) -> str:
        return f"room:{room_id}:answer_queue"

def _model_to_redis_hash(model: BaseModel) -> dict[str, str]:
    """将 Pydantic 模型序列化为 Redis Hash 兼容字符串映射。"""
    if isinstance(model, RoomStateCache):
        return model.to_redis_mapping()

    payload = model.model_dump(mode="json")
    mapping: dict[str, str] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            mapping[key] = json.dumps(value, ensure_ascii=False)
        else:
            mapping[key] = str(value)
    return mapping


class RedisRoomManager:
    """Redis 房间管理类（异步 + Pydantic 序列化）"""

    def _default_room_state(self) -> RoomStateCache:
        return RoomStateCache(host_player_id="")

    async def _load_room_state(
        self,
        room_id: str,
        create_if_missing: bool = False,
    ) -> Optional[RoomStateCache]:
        redis = await get_redis()
        if not redis:
            return None

        room_key = RedisKeys.room(room_id)
        fields = await redis.hgetall(room_key)
        if not fields:
            if create_if_missing:
                state = self._default_room_state()
                await self._save_room_state(room_id, state)
                return state
            return None

        return RoomStateCache.from_redis_hash(fields)

    async def _save_room_state(self, room_id: str,
                               state: RoomStateCache) -> bool:
        redis = await get_redis()
        if not redis:
            return False

        room_key = RedisKeys.room(room_id)
        await redis.hset(room_key, mapping=_model_to_redis_hash(state))
        await redis.expire(room_key, ROOM_TTL_SECONDS)
        return True

    async def get_room_info(self, room_id: str) -> Optional[RoomInfoCache]:
        redis = await get_redis()
        if not redis:
            return None

        try:
            state = await self._load_room_state(room_id)
            if state is None:
                return None

            players_key = RedisKeys.room_players(room_id)
            players = sorted(await redis.smembers(players_key))
            answer_queue = [
                item.player_id for item in sorted(
                    state.answer_queue,
                    key=lambda x: (x.offset_ts, x.server_ts),
                )
            ]

            return RoomInfoCache(
                room=state,
                players=players,
                answer_queue=answer_queue,
                answers={},
            )
        except Exception as e:
            print(f"Error getting room info: {e}")
            return None

    async def get_room_players(self, room_id: str) -> set[str]:
        redis = await get_redis()
        if not redis:
            return set()

        try:
            players_key = RedisKeys.room_players(room_id)
            return await redis.smembers(players_key)
        except Exception as e:
            print(f"Error getting room players: {e}")
            return set()

    async def get_current_song_index(self, room_id: str) -> Optional[int]:
        state = await self._load_room_state(room_id)
        return None if state is None else state.current_song_index

    async def update_playback_state(
        self,
        room_id: str,
        round_state: str,
        progress_ms: int,
        offset_ts: int,
        audio_url: str | None,
        event_ts: int,
        event_name: str,
    ) -> bool:
        try:
            state = await self._load_room_state(room_id,
                                                create_if_missing=True)
            if state is None:
                return False

            state.current_round_state = round_state
            state.play_progress = int(progress_ms)
            state.play_offset_ts = int(offset_ts)
            state.last_control_ts = int(event_ts)
            state.last_control_event = event_name
            if audio_url is not None:
                state.audio_url = audio_url

            return await self._save_room_state(room_id, state)
        except Exception as e:
            print(f"Error updating playback state: {e}")
            return False

    async def get_playback_state(self,
                                 room_id: str) -> Optional[RoomPlaybackState]:
        state = await self._load_room_state(room_id)
        if state is None:
            return None

        return RoomPlaybackState(
            round_state=state.current_round_state,
            progress_ms=state.play_progress,
            offset_ts=state.play_offset_ts,
            audio_url=state.audio_url,
        )

    async def get_play_progress(self, room_id: str) -> int:
        state = await self._load_room_state(room_id)
        return 0 if state is None else state.play_progress

    async def set_song_queue(self, room_id: str, song_ids: list[str]) -> bool:
        redis = await get_redis()
        if not redis:
            return False

        try:
            song_queue_key = RedisKeys.room_song_queue(room_id)
            await redis.delete(song_queue_key)
            if song_ids:
                await redis.rpush(song_queue_key, *song_ids)
            await redis.expire(song_queue_key, ROOM_TTL_SECONDS)
            return True
        except Exception as e:
            print(f"Error setting song queue: {e}")
            return False

    async def get_song_queue(self, room_id: str) -> list[str]:
        redis = await get_redis()
        if not redis:
            return []

        try:
            song_queue_key = RedisKeys.room_song_queue(room_id)
            return await redis.lrange(song_queue_key, 0, -1)
        except Exception as e:
            print(f"Error getting song queue: {e}")
            return []

    async def add_to_answer_queue(
        self,
        room_id: str,
        player_id: str,
        offset_ts: int,
        server_ts: Optional[int] = None,
    ) -> bool:
        try:
            state = await self._load_room_state(room_id,
                                                create_if_missing=True)
            if state is None:
                return False

            now_ts = int(time.time() *
                         1000) if server_ts is None else int(server_ts)

            if any(item.player_id == player_id for item in state.answer_queue):
                return True

            state.answer_queue.append(
                AnswerQueueItem(
                    player_id=player_id,
                    offset_ts=int(offset_ts),
                    server_ts=now_ts,
                ))
            return await self._save_room_state(room_id, state)
        except Exception as e:
            print(f"Error adding to answer queue: {e}")
            return False

    async def get_answer_queue(self, room_id: str) -> list[str]:
        try:
            sorted_items = await self.get_sorted_answer_queue(room_id)
            return [item.player_id for item in sorted_items]
        except Exception as e:
            print(f"Error getting answer queue: {e}")
            return []

    async def get_sorted_answer_queue(self,
                                      room_id: str) -> list[AnswerQueueItem]:
        try:
            state = await self._load_room_state(room_id)
            if state is None:
                return []

            return sorted(
                state.answer_queue,
                key=lambda x: (x.offset_ts, x.server_ts),
            )
        except Exception as e:
            print(f"Error getting sorted answer queue: {e}")
            return []

    async def remove_from_answer_queue(self, room_id: str,
                                       player_id: str) -> bool:
        try:
            state = await self._load_room_state(room_id,
                                                create_if_missing=True)
            if state is None:
                return False

            new_queue = [
                item for item in state.answer_queue
                if item.player_id != player_id
            ]
            if len(new_queue) == len(state.answer_queue):
                return True

            state.answer_queue = new_queue
            return await self._save_room_state(room_id, state)
        except Exception as e:
            print(f"Error removing from answer queue: {e}")
            return False

    async def clear_answer_queue(self, room_id: str) -> bool:
        try:
            state = await self._load_room_state(room_id,
                                                create_if_missing=True)
            if state is None:
                return False

            state.answer_queue = []
            return await self._save_room_state(room_id, state)
        except Exception as e:
            print(f"Error clearing answer queue: {e}")
            return False

    async def set_current_answerer(self, room_id: str, player_id: str) -> bool:
        try:
            state = await self._load_room_state(room_id,
                                                create_if_missing=True)
            if state is None:
                return False

            state.current_answerer = player_id
            return await self._save_room_state(room_id, state)
        except Exception as e:
            print(f"Error setting current answerer: {e}")
            return False

    async def get_current_answerer(self, room_id: str) -> Optional[str]:
        state = await self._load_room_state(room_id)
        if state is None:
            return None
        return state.current_answerer

    async def set_player_online(self, room_id: str, player_id: str) -> bool:
        """将玩家标记为在线。"""
        redis = await get_redis()
        if not redis:
            return False

        try:
            key = RedisKeys.room_online(room_id)
            existed_payload = await redis.hget(key, player_id)
            if existed_payload:
                existed_state = PlayerOnlineState.model_validate_json(
                    existed_payload)
                state = PlayerOnlineState(
                    player_id=player_id,
                    room_id=room_id,
                    is_online=True,
                    connected_at=existed_state.connected_at,
                )
            else:
                state = PlayerOnlineState(player_id=player_id,
                                          room_id=room_id,
                                          is_online=True)

            state.updated_at = _utc_now_iso()
            await redis.hset(key, player_id, state.model_dump_json())
            await redis.expire(key, ROOM_TTL_SECONDS)
            return True
        except Exception as e:
            print(f"Error setting player online: {e}")
            return False

    async def set_player_offline(self, room_id: str, player_id: str) -> bool:
        """将玩家标记为离线。"""
        redis = await get_redis()
        if not redis:
            return False

        try:
            key = RedisKeys.room_online(room_id)
            existed_payload = await redis.hget(key, player_id)
            if existed_payload:
                existed_state = PlayerOnlineState.model_validate_json(
                    existed_payload)
                state = PlayerOnlineState(
                    player_id=player_id,
                    room_id=room_id,
                    is_online=False,
                    connected_at=existed_state.connected_at,
                )
            else:
                state = PlayerOnlineState(player_id=player_id,
                                          room_id=room_id,
                                          is_online=False)

            state.updated_at = _utc_now_iso()
            await redis.hset(key, player_id, state.model_dump_json())
            await redis.expire(key, ROOM_TTL_SECONDS)
            return True
        except Exception as e:
            print(f"Error setting player offline: {e}")
            return False

    async def get_room_online_snapshot(self,
                                       room_id: str) -> RoomOnlineSnapshot:
        """获取房间在线玩家快照。"""
        redis = await get_redis()
        if not redis:
            return RoomOnlineSnapshot(room_id=room_id, online_players=[])

        try:
            key = RedisKeys.room_online(room_id)
            raw_map = await redis.hgetall(key)
            online_players: list[str] = []
            for player_id, payload in raw_map.items():
                try:
                    state = PlayerOnlineState.model_validate_json(payload)
                    if state.is_online:
                        online_players.append(player_id)
                except Exception:
                    continue

            return RoomOnlineSnapshot(
                room_id=room_id,
                online_players=sorted(online_players),
            )
        except Exception as e:
            print(f"Error getting room online snapshot: {e}")
            return RoomOnlineSnapshot(room_id=room_id, online_players=[])

    async def set_all_offline_by_room(self, room_id: str) -> bool:
        """将房间内所有已记录玩家标记为离线。"""
        redis = await get_redis()
        if not redis:
            return False

        try:
            key = RedisKeys.room_online(room_id)
            raw_map = await redis.hgetall(key)
            if not raw_map:
                return True

            updates: dict[str, str] = {}
            for player_id, payload in raw_map.items():
                try:
                    state = PlayerOnlineState.model_validate_json(payload)
                    state.is_online = False
                    state.updated_at = _utc_now_iso()
                    updates[player_id] = state.model_dump_json()
                except Exception:
                    updates[player_id] = PlayerOnlineState(
                        player_id=player_id,
                        room_id=room_id,
                        is_online=False,
                    ).model_dump_json()

            await redis.hset(key, mapping=updates)
            await redis.expire(key, ROOM_TTL_SECONDS)
            return True
        except Exception as e:
            print(f"Error setting all offline by room: {e}")
            return False

    async def set_all_offline(self) -> bool:
        """将所有房间的在线状态统一标记为离线（用于服务启动/关闭时纠偏）。"""
        redis = await get_redis()
        if not redis:
            return False

        try:
            async for key in redis.scan_iter(match="room:*:online"):
                raw_map = await redis.hgetall(key)
                if not raw_map:
                    continue

                key_parts = key.split(":")
                room_id = key_parts[1] if len(key_parts) >= 3 else ""

                updates: dict[str, str] = {}
                for player_id, payload in raw_map.items():
                    try:
                        state = PlayerOnlineState.model_validate_json(payload)
                        state.is_online = False
                        state.updated_at = _utc_now_iso()
                        updates[player_id] = state.model_dump_json()
                    except Exception:
                        updates[player_id] = PlayerOnlineState(
                            player_id=player_id,
                            room_id=room_id,
                            is_online=False,
                        ).model_dump_json()

                if updates:
                    await redis.hset(key, mapping=updates)
                    await redis.expire(key, ROOM_TTL_SECONDS)
            return True
        except Exception as e:
            print(f"Error setting all offline: {e}")
            return False


room_manager = RedisRoomManager()
