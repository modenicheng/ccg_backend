"""
日志配置模块
提供项目统一的日志配置和管理功能
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
from rich.logging import RichHandler

log_level_map = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def init_logging(
    *,
    level: int = logging.INFO,
    log_dir: str | Path = "logs",
    log_file: str = "app.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    rich_markup: bool = True,
) -> None:
    """初始化根日志器。

    行为说明：
    - 始终更新 root logger 的日志级别；
    - Rich 控制台处理器仅在不存在时添加；
    - 轮转文件处理器按目标日志文件路径去重，避免重复添加。

    Args:
        level: 根日志级别。
        log_dir: 日志目录。
        log_file: 日志文件名。
        max_bytes: 单个日志文件最大字节数。
        backup_count: 轮转备份文件数量。
        rich_markup: 是否启用 Rich 的 markup 渲染。
    """

    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    log_file_path = log_dir_path / log_file

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not any(isinstance(handler, RichHandler) for handler in root_logger.handlers):
        rich_handler = RichHandler(
            rich_tracebacks=True,
            markup=rich_markup,
            show_time=True,
            show_level=True,
            show_path=True,
        )
        rich_handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger.addHandler(rich_handler)

    has_same_file_handler = any(
        isinstance(handler, RotatingFileHandler) and
        Path(handler.baseFilename).resolve() == log_file_path.resolve()
        for handler in root_logger.handlers)
    if not has_same_file_handler:
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
        root_logger.addHandler(file_handler)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a logger with the given name."""
    return logging.getLogger(name)
