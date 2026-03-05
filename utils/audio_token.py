"""
音频令牌管理工具
生成临时音频访问令牌，保护音频ID不被直接暴露
"""

import os
import uuid
import time
import json
from typing import Optional
from cache.connection import get_redis

# 环境变量
AUDIO_TOKEN_PREFIX = "audio_token:"
AUDIO_TOKEN_TTL = int(os.getenv("CCG_AUDIO_TOKEN_TTL", "21600"))  # 默认6小时





async def get_song_id_from_token(token: str) -> Optional[int]:
    """
    从令牌获取song_id，验证有效性
    
    Args:
        token: 音频访问令牌
        
    Returns:
        int: song_id 或 None(无效)
    """
    redis = await get_redis()
    key = f"{AUDIO_TOKEN_PREFIX}{token}"
    
    # 获取令牌数据
    data_json = await redis.get(key)
    if not data_json:
        return None
    
    try:
        data = json.loads(data_json)
        song_id = data["song_id"]
        created_at = data["created_at"]
        
        # 额外过期检查（双重保障）
        current_time = int(time.time())
        if current_time - created_at > AUDIO_TOKEN_TTL:
            await redis.delete(key)  # 清理过期令牌
            return None
            
        return song_id
    except (json.JSONDecodeError, KeyError):
        await redis.delete(key)  # 清理无效数据
        return None


def get_audio_stream_url(token: str) -> str:
    """
    生成音频流URL
    
    Args:
        token: 音频访问令牌
        
    Returns:
        str: 音频流URL，格式: /api/songs/stream/{token}
    """
    return f"/api/songs/stream/{token}"


async def generate_audio_token(song_id: int) -> str:
    """
    生成音频访问令牌
    
    Args:
        song_id: 歌曲ID
        
    Returns:
        str: 随机UUID令牌
    """
    token = str(uuid.uuid4())
    redis = await get_redis()
    
    # 存储映射: token -> {song_id, created_at}
    data = {
        "song_id": song_id,
        "created_at": int(time.time())
    }
    
    await redis.setex(
        f"{AUDIO_TOKEN_PREFIX}{token}",
        AUDIO_TOKEN_TTL,
        json.dumps(data)
    )
    
    return token