from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from rich.logging import RichHandler

_CONFIGURED = False


def init_logging(
    *,
    level: int = logging.INFO,
    log_dir: str | Path = "logs",
    log_file: str = "app.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    rich_markup: bool = True,
) -> None:
    """Initialize logging with Rich console output and rotating file logs."""
    global _CONFIGURED
    if _CONFIGURED:
        return

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

    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    root_logger.addHandler(file_handler)

    _CONFIGURED = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a logger with the given name."""
    return logging.getLogger(name)
