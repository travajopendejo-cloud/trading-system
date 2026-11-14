#!/usr/bin/env python3
"""Main entrypoint for the multi-bot trading system."""

from __future__ import annotations

import asyncio
import signal
import sys

from core.config import load_global_config, get_settings
from core.logging_setup import setup_logging, get_logger
from core.orchestrator import Orchestrator

logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    global_cfg = load_global_config("config/global.yaml")

    setup_logging(
        level=settings.log_level or global_cfg.logging.get("level", "INFO"),
        json_format=settings.log_json if settings.log_json is not None else global_cfg.logging.get("json_format", True),
        log_dir=global_cfg.logging.get("log_dir", "logs"),
        max_bytes=global_cfg.logging.get("max_bytes", 10_485_760),
        backup_count=global_cfg.logging.get("backup_count", 5),
    )

    logger.info(
        "starting_trading_system",
        mode=settings.trading_mode,
        exchange=global_cfg.exchange.get("id"),
    )

    orchestrator = Orchestrator(global_cfg)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        await orchestrator.start()
        await stop_event.wait()
    finally:
        await orchestrator.stop()
        logger.info("shutdown_complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
