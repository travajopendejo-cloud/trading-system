"""Meme Sniper entry point – supports both filter-based sniping and Wallet Copy."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import yaml

from core.sniper import MemeSniper
from core.monitor import MockMonitor
from core.wallet_copy import CopySignal

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

    logger.info("=" * 55)
    logger.info("Meme Sniper started in %s mode", config.get("mode", "paper"))
    logger.info("Capital: $%.2f | Max position: $%.2f", 
                sniper.risk.capital, sniper.risk.max_position_usd)
    logger.info("Wallet Copy module loaded with %d tracked wallets", 
                len(sniper.wallet_copy.wallets))
    logger.info("=" * 55)

    async for token in monitor.stream_new_tokens():
        logger.info(
            "New token: %s | Price: %.8f | Liq: $%.0f | Dev: %.1f%%",
            token.symbol, token.price, token.liquidity_usd, token.dev_buy_pct
        )

        # 1. Normal filter-based evaluation
        position = sniper.open_trade(token, source="filter")

        # 2. Simulate a wallet copy signal (for testing)
        # Later this will come from real wallet monitoring
        if position is None and token.dev_buy_pct < 6:
            # Pretend one of our tracked wallets bought it
            fake_signal = CopySignal(
                wallet_name="Hermes",
                wallet_address="Hermes_wallet_placeholder",
                token_address=token.address,
                token_symbol=token.symbol,
                entry_price=token.price,
                amount_usd=45.0,
                reason="Simulated copy for testing"
            )
            sniper.handle_copy_signal(fake_signal)

        # Check exits on open positions
        for symbol in list(sniper.risk.positions.keys()):
            import random
            current_price = sniper.risk.positions[symbol].entry_price * random.uniform(0.65, 2.4)
            sniper.check_position(symbol, current_price)


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Sniper stopped by user")


if __name__ == "__main__":
    main()
