import os
import redis
from typing import Optional


class RedisClient:
    """Redis 客户端管理类"""

    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self.connected = False

    def connect(self, url: Optional[str] = None) -> bool:
        """连接到 Redis
        
        Args:
            url: Redis 连接 URL，默认为环境变量中的 CCG_REDIS_URL
            
        Returns:
            bool: 连接是否成功
        """
        try:
            redis_url = url or os.getenv("CCG_REDIS_URL",
                                         "redis://localhost:6379/0")
            self.client = redis.from_url(redis_url, decode_responses=True)
            # 测试连接
            self.client.ping()
            self.connected = True
            return True
        except Exception as e:
            print(f"Failed to connect to Redis: {e}")
            self.connected = False
            return False

    def disconnect(self):
        """断开 Redis 连接"""
        if self.client:
            try:
                self.client.close()
            except Exception as e:
                print(f"Error closing Redis connection: {e}")
            finally:
                self.client = None
                self.connected = False

    def get_client(self) -> Optional[redis.Redis]:
        """获取 Redis 客户端实例
        
        Returns:
            Optional[redis.Redis]: Redis 客户端实例
        """
        if not self.connected:
            self.connect()
        return self.client

    def is_connected(self) -> bool:
        """检查是否连接到 Redis
        
        Returns:
            bool: 是否连接
        """
        return self.connected


# 创建全局 Redis 客户端实例
redis_client = RedisClient()


def get_redis() -> Optional[redis.Redis]:
    """获取 Redis 客户端的快捷函数
    
    Returns:
        Optional[redis.Redis]: Redis 客户端实例
    """
    return redis_client.get_client()
