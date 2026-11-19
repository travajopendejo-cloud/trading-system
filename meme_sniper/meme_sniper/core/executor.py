"""Execution layer for the Meme Sniper (paper mode for now)."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class Executor:
    def __init__(self, mode: str = "paper"):
        self.mode = mode
        self.paper_balance = 100.0  # will be overwritten by risk manager

    def buy(self, symbol: str, usd_amount: float, price: float) -> dict:
        """
        Simulate or execute a buy.
        Returns order info.
        """
        amount = usd_amount / price if price > 0 else 0

        if self.mode == "paper":
            logger.info(f"[PAPER] BUY {symbol} | ${usd_amount:.2f} @ {price:.8f} → {amount:.4f} tokens")
            return {
                "status": "filled",
                "symbol": symbol,
                "side": "buy",
                "amount": amount,
                "price": price,
                "usd_value": usd_amount,
                "mode": "paper",
            }

        # Live mode will be added later
        raise NotImplementedError("Live execution not implemented yet")

    def sell(self, symbol: str, amount: float, price: float) -> dict:
        """
        Simulate or execute a sell.
        """
        usd_value = amount * price

        if self.mode == "paper":
            logger.info(f"[PAPER] SELL {symbol} | {amount:.4f} tokens @ {price:.8f} → ${usd_value:.2f}")
            return {
                "status": "filled",
                "symbol": symbol,
                "side": "sell",
                "amount": amount,
                "price": price,
                "usd_value": usd_value,
                "mode": "paper",
            }

        raise NotImplementedError("Live execution not implemented yet")
