"""Redis client connection management module."""

from __future__ import annotations

import asyncio
from typing import Awaitable, cast
from urllib.parse import urlparse

import redis.asyncio as redis
from redis.asyncio import Redis

from config import app_config
from utils.logger import get_logger

logger = get_logger(__name__)


class RedisClient:
    """Redis 客户端管理类"""

    def __init__(self):
        self.client: Redis | None = None
        self.connected = False
        self._connect_lock = asyncio.Lock()

    @staticmethod
    def _is_local_redis_endpoint(redis_url: str) -> bool:
        parsed = urlparse(redis_url)
        host = parsed.hostname
        return host in {"localhost", "127.0.0.1", "::1"} or parsed.scheme == "unix"

    async def connect(self, url: str | None = None) -> bool:
        """连接到 Redis

        Args:
            url: Redis 连接 URL，默认为环境变量中的 CCG_REDIS_URL

        Returns:
            bool: 连接是否成功
        """
        try:
            redis_url = url or app_config.redis_url
            parsed = urlparse(redis_url)
            if parsed.scheme == "redis" and not self._is_local_redis_endpoint(
                    redis_url):
                logger.warning("Redis URL is using redis:// over non-local endpoint. "
                               "Use rediss:// in production for TLS encryption.")
            self.client = redis.from_url(redis_url, decode_responses=True)
            # 测试连接
            await cast(Awaitable[bool], self.client.ping())
            self.connected = True
            return True
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Failed to connect to Redis: %s", e)
            self.connected = False
            return False

    async def disconnect(self) -> None:
        """断开 Redis 连接"""
        if self.client:
            try:
                await self.client.aclose()
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Error closing Redis connection: %s", e)
            finally:
                self.client = None
                self.connected = False

    async def get_client(self) -> Redis:
        """获取 Redis 客户端实例

        Returns:
            redis.Redis: Redis 客户端实例
        """
        if self.connected and self.client is not None:
            return self.client
        async with self._connect_lock:
            # Double-check after acquiring lock
            if self.connected and self.client is not None:
                return self.client
            await self.connect()
        if not self.connected or self.client is None:
            raise RuntimeError("Unable to connect to Redis")
        return self.client

    def is_connected(self) -> bool:
        """检查是否连接到 Redis

        Returns:
            bool: 是否连接
        """
        return self.connected


# 创建全局 Redis 客户端实例
redis_client = RedisClient()


async def get_redis() -> Redis:
    """获取 Redis 客户端的快捷函数

    Returns:
        redis.asyncio: Redis 客户端实例
    """
    r = await redis_client.get_client()
    if r is None:
        raise RuntimeError("Failed to get Redis client")
    return r
