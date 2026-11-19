"""Meme Sniper entry point."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import yaml

from core.sniper import MemeSniper
from core.monitor import MockMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


async def run():
    config = load_config()
    sniper = MemeSniper(config)
    monitor = MockMonitor()

    logger.info("=" * 50)
    logger.info("Meme Sniper started in %s mode", config.get("mode", "paper"))
    logger.info("Capital: $%.2f | Max position: $%.2f", 
                sniper.risk.capital, sniper.risk.max_position_usd)
    logger.info("=" * 50)

    async for token in monitor.stream_new_tokens():
        logger.info(
            "New token detected: %s | Price: %.8f | Liq: $%.0f | Dev: %.1f%%",
            token.symbol, token.price, token.liquidity_usd, token.dev_buy_pct
        )

        # Try to open a trade
        position = sniper.open_trade(token)

        if position:
            logger.info("→ Position opened on %s", token.symbol)

        # Check existing positions for exits (simple version)
        for symbol in list(sniper.risk.positions.keys()):
            # Simulate a random price move for testing
            import random
            current_price = sniper.risk.positions[symbol].entry_price * random.uniform(0.7, 2.2)
            sniper.check_position(symbol, current_price)


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Sniper stopped by user")


if __name__ == "__main__":
    main()
