from . import dataframe
from . import enumerations

from .dataframe import get_event_type
from .logger import get_logger, init_logging

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
]

# 如果有内存监控模块，添加到导出列表
if HAS_MEMORY_MONITOR:
    __all__.extend([
        "MemoryMonitor",
        "start_memory_monitoring",
        "periodic_memory_report",
    ])
