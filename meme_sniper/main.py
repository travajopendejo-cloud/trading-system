"""Run the meme sniper by itself. The main trading app starts it too.

Paper mode only. Ctrl+C stops the loop.
"""

from __future__ import annotations

import asyncio
import logging

from meme_sniper.core.service import MemeSniperService
from meme_sniper.core.settings import load_sniper_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    config = load_sniper_config()
    service = MemeSniperService(config)
    logger.info("Starting meme sniper in paper mode")
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        logger.info("Sniper stopped by user")


if __name__ == "__main__":
    main()
