import json
from typing import Dict, List, Optional, Set, Any
from .connection import get_redis


class RedisKeys:
    """Redis 键名生成类"""
    
    @staticmethod
    def room(room_id: str) -> str:
        """房间实时状态键"""
        return f"room:{room_id}"
    
    @staticmethod
    def room_players(room_id: str) -> str:
        """房间玩家集合键"""
        return f"room:{room_id}:players"
    
    @staticmethod
    def room_ready(room_id: str) -> str:
        """房间玩家准备状态键"""
        return f"room:{room_id}:ready"
    
    @staticmethod
    def player_ws(player_id: str) -> str:
        """玩家 WebSocket 连接映射键"""
        return f"player:{player_id}:ws"
    
    @staticmethod
    def room_song_queue(room_id: str) -> str:
        """房间歌曲队列键"""
        return f"room:{room_id}:song_queue"
    
    @staticmethod
    def session(token: str) -> str:
        """会话映射键"""
        return f"session:{token}"


class RedisRoomManager:
    """Redis 房间管理类"""
    
    def __init__(self):
        self.redis = get_redis()
    
    def create_room(self, room_id: str, host_player_id: str) -> bool:
        """创建房间
        
        Args:
            room_id: 房间 ID
            host_player_id: 房主玩家 ID
            
        Returns:
            bool: 是否创建成功
        """
        if not self.redis:
            return False
        
        try:
            # 创建房间基本信息
            room_key = RedisKeys.room(room_id)
            self.redis.hset(room_key, mapping={
                "host_player_id": host_player_id,
                "status": "waiting",
                "current_song_index": 0,
                "current_round_state": "playing",
                "play_progress": 0,
                "tag_groups": "{}",
                "current_answerer": "",
            })
            
            # 设置房间过期时间（6小时）
            self.redis.expire(room_key, 6 * 60 * 60)
            
            # 创建玩家集合
            players_key = RedisKeys.room_players(room_id)
            self.redis.sadd(players_key, host_player_id)
            self.redis.expire(players_key, 6 * 60 * 60)
            
            # 创建玩家准备状态
            ready_key = RedisKeys.room_ready(room_id)
            self.redis.hset(ready_key, host_player_id, "true")
            self.redis.expire(ready_key, 6 * 60 * 60)
            
            # 创建歌曲队列
            song_queue_key = RedisKeys.room_song_queue(room_id)
            self.redis.expire(song_queue_key, 6 * 60 * 60)
            
            return True
        except Exception as e:
            print(f"Error creating room: {e}")
            return False
    
    def add_player(self, room_id: str, player_id: str) -> bool:
        """添加玩家到房间
        
        Args:
            room_id: 房间 ID
            player_id: 玩家 ID
            
        Returns:
            bool: 是否添加成功
        """
        if not self.redis:
            return False
        
        try:
            # 添加到玩家集合
            players_key = RedisKeys.room_players(room_id)
            self.redis.sadd(players_key, player_id)
            
            # 设置准备状态为 false
            ready_key = RedisKeys.room_ready(room_id)
            self.redis.hset(ready_key, player_id, "false")
            
            return True
        except Exception as e:
            print(f"Error adding player: {e}")
            return False
    
    def remove_player(self, room_id: str, player_id: str) -> bool:
        """从房间移除玩家
        
        Args:
            room_id: 房间 ID
            player_id: 玩家 ID
            
        Returns:
            bool: 是否移除成功
        """
        if not self.redis:
            return False
        
        try:
            # 从玩家集合移除
            players_key = RedisKeys.room_players(room_id)
            self.redis.srem(players_key, player_id)
            
            # 从准备状态移除
            ready_key = RedisKeys.room_ready(room_id)
            self.redis.hdel(ready_key, player_id)
            
            return True
        except Exception as e:
            print(f"Error removing player: {e}")
            return False
    
    def get_room_players(self, room_id: str) -> Set[str]:
        """获取房间玩家列表
        
        Args:
            room_id: 房间 ID
            
        Returns:
            Set[str]: 玩家 ID 集合
        """
        if not self.redis:
            return set()
        
        try:
            players_key = RedisKeys.room_players(room_id)
            return self.redis.smembers(players_key)
        except Exception as e:
            print(f"Error getting room players: {e}")
            return set()
    
    def set_player_ready(self, room_id: str, player_id: str, ready: bool) -> bool:
        """设置玩家准备状态
        
        Args:
            room_id: 房间 ID
            player_id: 玩家 ID
            ready: 是否准备好
            
        Returns:
            bool: 是否设置成功
        """
        if not self.redis:
            return False
        
        try:
            ready_key = RedisKeys.room_ready(room_id)
            self.redis.hset(ready_key, player_id, "true" if ready else "false")
            return True
        except Exception as e:
            print(f"Error setting player ready: {e}")
            return False
    
    def get_player_ready(self, room_id: str, player_id: str) -> bool:
        """获取玩家准备状态
        
        Args:
            room_id: 房间 ID
            player_id: 玩家 ID
            
        Returns:
            bool: 是否准备好
        """
        if not self.redis:
            return False
        
        try:
            ready_key = RedisKeys.room_ready(room_id)
            value = self.redis.hget(ready_key, player_id)
            return value == "true"
        except Exception as e:
            print(f"Error getting player ready: {e}")
            return False
    
    def get_all_players_ready(self, room_id: str) -> bool:
        """检查所有玩家是否都准备好
        
        Args:
            room_id: 房间 ID
            
        Returns:
            bool: 是否所有玩家都准备好
        """
        if not self.redis:
            return False
        
        try:
            players = self.get_room_players(room_id)
            if not players:
                return False
            
            ready_key = RedisKeys.room_ready(room_id)
            for player_id in players:
                if not self.get_player_ready(room_id, player_id):
                    return False
            
            return True
        except Exception as e:
            print(f"Error checking all players ready: {e}")
            return False
    
    def update_room_status(self, room_id: str, status: str) -> bool:
        """更新房间状态
        
        Args:
            room_id: 房间 ID
            status: 房间状态 (waiting/playing/ended)
            
        Returns:
            bool: 是否更新成功
        """
        if not self.redis:
            return False
        
        try:
            room_key = RedisKeys.room(room_id)
            self.redis.hset(room_key, "status", status)
            return True
        except Exception as e:
            print(f"Error updating room status: {e}")
            return False
    
    def get_room_status(self, room_id: str) -> Optional[str]:
        """获取房间状态
        
        Args:
            room_id: 房间 ID
            
        Returns:
            Optional[str]: 房间状态
        """
        if not self.redis:
            return None
        
        try:
            room_key = RedisKeys.room(room_id)
            return self.redis.hget(room_key, "status")
        except Exception as e:
            print(f"Error getting room status: {e}")
            return None
    
    def update_play_progress(self, room_id: str, progress: int) -> bool:
        """更新播放进度
        
        Args:
            room_id: 房间 ID
            progress: 播放进度（毫秒）
            
        Returns:
            bool: 是否更新成功
        """
        if not self.redis:
            return False
        
        try:
            room_key = RedisKeys.room(room_id)
            self.redis.hset(room_key, "play_progress", progress)
            return True
        except Exception as e:
            print(f"Error updating play progress: {e}")
            return False
    
    def get_play_progress(self, room_id: str) -> int:
        """获取播放进度
        
        Args:
            room_id: 房间 ID
            
        Returns:
            int: 播放进度（毫秒）
        """
        if not self.redis:
            return 0
        
        try:
            room_key = RedisKeys.room(room_id)
            value = self.redis.hget(room_key, "play_progress")
            return int(value) if value else 0
        except Exception as e:
            print(f"Error getting play progress: {e}")
            return 0
    
    def set_song_queue(self, room_id: str, song_ids: List[str]) -> bool:
        """设置歌曲队列
        
        Args:
            room_id: 房间 ID
            song_ids: 歌曲 ID 列表
            
        Returns:
            bool: 是否设置成功
        """
        if not self.redis:
            return False
        
        try:
            song_queue_key = RedisKeys.room_song_queue(room_id)
            # 清空现有队列
            self.redis.delete(song_queue_key)
            # 添加歌曲到队列
            if song_ids:
                self.redis.lpush(song_queue_key, *song_ids)
            return True
        except Exception as e:
            print(f"Error setting song queue: {e}")
            return False
    
    def get_song_queue(self, room_id: str) -> List[str]:
        """获取歌曲队列
        
        Args:
            room_id: 房间 ID
            
        Returns:
            List[str]: 歌曲 ID 列表
        """
        if not self.redis:
            return []
        
        try:
            song_queue_key = RedisKeys.room_song_queue(room_id)
            return self.redis.lrange(song_queue_key, 0, -1)
        except Exception as e:
            print(f"Error getting song queue: {e}")
            return []
    
    def add_to_answer_queue(self, room_id: str, player_id: str) -> bool:
        """添加玩家到抢答队列
        
        Args:
            room_id: 房间 ID
            player_id: 玩家 ID
            
        Returns:
            bool: 是否添加成功
        """
        if not self.redis:
            return False
        
        try:
            room_key = RedisKeys.room(room_id)
            # 获取当前队列
            queue_str = self.redis.hget(room_key, "answer_queue")
            queue = json.loads(queue_str) if queue_str else []
            
            # 检查玩家是否已经在队列中
            if player_id not in queue:
                queue.append(player_id)
                self.redis.hset(room_key, "answer_queue", json.dumps(queue))
            
            return True
        except Exception as e:
            print(f"Error adding to answer queue: {e}")
            return False
    
    def get_answer_queue(self, room_id: str) -> List[str]:
        """获取抢答队列
        
        Args:
            room_id: 房间 ID
            
        Returns:
            List[str]: 玩家 ID 列表
        """
        if not self.redis:
            return []
        
        try:
            room_key = RedisKeys.room(room_id)
            queue_str = self.redis.hget(room_key, "answer_queue")
            return json.loads(queue_str) if queue_str else []
        except Exception as e:
            print(f"Error getting answer queue: {e}")
            return []
    
    def clear_answer_queue(self, room_id: str) -> bool:
        """清空抢答队列
        
        Args:
            room_id: 房间 ID
            
        Returns:
            bool: 是否清空成功
        """
        if not self.redis:
            return False
        
        try:
            room_key = RedisKeys.room(room_id)
            self.redis.hset(room_key, "answer_queue", "[]")
            return True
        except Exception as e:
            print(f"Error clearing answer queue: {e}")
            return False
    
    def set_current_answerer(self, room_id: str, player_id: str) -> bool:
        """设置当前作答玩家
        
        Args:
            room_id: 房间 ID
            player_id: 玩家 ID
            
        Returns:
            bool: 是否设置成功
        """
        if not self.redis:
            return False
        
        try:
            room_key = RedisKeys.room(room_id)
            self.redis.hset(room_key, "current_answerer", player_id)
            return True
        except Exception as e:
            print(f"Error setting current answerer: {e}")
            return False
    
    def get_current_answerer(self, room_id: str) -> Optional[str]:
        """获取当前作答玩家
        
        Args:
            room_id: 房间 ID
            
        Returns:
            Optional[str]: 玩家 ID
        """
        if not self.redis:
            return None
        
        try:
            room_key = RedisKeys.room(room_id)
            return self.redis.hget(room_key, "current_answerer")
        except Exception as e:
            print(f"Error getting current answerer: {e}")
            return None


class RedisSessionManager:
    """Redis 会话管理类"""
    
    def __init__(self):
        self.redis = get_redis()
    
    def create_session(self, token: str, room_id: str, player_id: str) -> bool:
        """创建会话
        
        Args:
            token: 会话令牌
            room_id: 房间 ID
            player_id: 玩家 ID
            
        Returns:
            bool: 是否创建成功
        """
        if not self.redis:
            return False
        
        try:
            session_key = RedisKeys.session(token)
            session_data = f"{room_id}:{player_id}"
            # 设置会话，过期时间 24 小时
            self.redis.setex(session_key, 24 * 60 * 60, session_data)
            return True
        except Exception as e:
            print(f"Error creating session: {e}")
            return False
    
    def get_session(self, token: str) -> Optional[Dict[str, str]]:
        """获取会话信息
        
        Args:
            token: 会话令牌
            
        Returns:
            Optional[Dict[str, str]]: 包含 room_id 和 player_id 的字典
        """
        if not self.redis:
            return None
        
        try:
            session_key = RedisKeys.session(token)
            session_data = self.redis.get(session_key)
            if not session_data:
                return None
            
            # 解析会话数据
            parts = session_data.split(":")
            if len(parts) != 2:
                return None
            
            return {
                "room_id": parts[0],
                "player_id": parts[1]
            }
        except Exception as e:
            print(f"Error getting session: {e}")
            return None
    
    def delete_session(self, token: str) -> bool:
        """删除会话
        
        Args:
            token: 会话令牌
            
        Returns:
            bool: 是否删除成功
        """
        if not self.redis:
            return False
        
        try:
            session_key = RedisKeys.session(token)
            self.redis.delete(session_key)
            return True
        except Exception as e:
            print(f"Error deleting session: {e}")
            return False


# 创建全局实例
room_manager = RedisRoomManager()
session_manager = RedisSessionManager()
