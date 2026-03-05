from __future__ import annotations
import time as time_module


def get_ts_ms():
    """获取当前时间戳（毫秒）"""
    return int(time_module.time() * 1000)
