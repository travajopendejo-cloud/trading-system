"""Paper fills only. Live orders are rejected and never built."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Executor:
    def __init__(self, mode: str = "paper"):
        if str(mode).lower() != "paper":
            logger.warning(
                "Live execution was requested (%s) and refused. Staying in paper mode.",
                mode,
            )
        self.mode = "paper"

    def buy(self, symbol: str, usd_amount: float, price: float) -> dict:
        if self.mode != "paper":
            logger.error("Blocked a non-paper buy of %s", symbol)
            return {"status": "rejected", "reason": "live trading is disabled", "mode": self.mode}
        if usd_amount <= 0 or price <= 0:
            return {"status": "rejected", "reason": "bad price or size", "mode": "paper"}
        amount = usd_amount / price
        logger.info(
            "[PAPER] BUY %s | $%.2f @ %.8f -> %.4f tokens",
            symbol,
            usd_amount,
            price,
            amount,
        )
        return {
            "status": "filled",
            "symbol": symbol,
            "side": "buy",
            "amount": amount,
            "price": price,
            "usd_value": usd_amount,
            "mode": "paper",
        }

    def sell(self, symbol: str, amount: float, price: float) -> dict:
        if self.mode != "paper":
            logger.error("Blocked a non-paper sell of %s", symbol)
            return {"status": "rejected", "reason": "live trading is disabled", "mode": self.mode}
        if amount <= 0 or price < 0:
            return {"status": "rejected", "reason": "bad price or size", "mode": "paper"}
        usd_value = amount * price
        logger.info(
            "[PAPER] SELL %s | %.4f tokens @ %.8f -> $%.2f",
            symbol,
            amount,
            price,
            usd_value,
        )
        return {
            "status": "filled",
            "symbol": symbol,
            "side": "sell",
            "amount": amount,
            "price": price,
            "usd_value": usd_value,
            "mode": "paper",
        }
