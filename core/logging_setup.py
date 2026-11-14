"""Logging setup – works with or without structlog."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

try:
    import structlog
    HAS_STRUCTLOG = True
except ImportError:
    HAS_STRUCTLOG = False


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
    log_dir: str = "logs",
    max_bytes: int = 10_485_760,
    backup_count: int = 5,
) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)

    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        fh = logging.handlers.RotatingFileHandler(
            filename=Path(log_dir) / "trading_system.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass

    if HAS_STRUCTLOG:
        shared: list[Any] = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
        ]
        renderer = (
            structlog.processors.JSONRenderer()
            if json_format
            else structlog.dev.ConsoleRenderer(colors=True)
        )
        structlog.configure(
            processors=shared + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
        formatter = structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared,
        )
        for h in root.handlers:
            h.setFormatter(formatter)

    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str | None = None):
    if HAS_STRUCTLOG:
        return structlog.get_logger(name)
    return logging.getLogger(name or "trading")
