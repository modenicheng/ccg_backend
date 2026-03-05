from __future__ import annotations
from . import dataframe, enumerations

from .dataframe import get_event_type
from .logger import get_logger, init_logging
from .cookie import parse_cookie_string
from .audio_token import (
    generate_audio_token,
    get_song_id_from_token,
    get_audio_stream_url,
)

# 导入内存监控模块
try:
    from .memory_monitor import (
        MemoryMonitor,
        start_memory_monitoring,
        periodic_memory_report,
    )

    HAS_MEMORY_MONITOR = True
except ImportError:
    HAS_MEMORY_MONITOR = False

__all__ = [
    "dataframe",
    "enumerations",
    "get_event_type",
    "get_logger",
    "init_logging",
    "parse_cookie_string",
    "generate_audio_token",
    "get_song_id_from_token",
    "get_audio_stream_url",
]

# 如果有内存监控模块，添加到导出列表
if HAS_MEMORY_MONITOR:
    __all__.extend([
        "MemoryMonitor",
        "start_memory_monitoring",
        "periodic_memory_report",
    ])
