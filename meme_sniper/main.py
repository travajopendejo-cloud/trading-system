"""Meme Sniper entry point."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import yaml

from core.sniper import MemeSniper
from core.filters import TokenInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def main():
    config = load_config()
    sniper = MemeSniper(config)

    logger.info("Meme Sniper started in %s mode", config.get("mode", "paper"))
    logger.info("Capital: $%.2f | Max position: $%.2f", 
                sniper.risk.capital, sniper.risk.max_position_usd)

    # Placeholder loop – real monitoring will be added next
    logger.info("Waiting for new tokens... (monitoring not yet connected)")
    
    while True:
        # Later we will plug in real pump.fun / migration feed here
        time.sleep(10)


if __name__ == "__main__":
    main()
