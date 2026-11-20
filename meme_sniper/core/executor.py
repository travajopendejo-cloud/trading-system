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
        self.cost_rate = 0.0
        self.delay_seconds = 0.0

    def _fill_price(self, price: float, side: str) -> float:
        # Paper fills are worse than the price we saw, by the fee plus slippage.
        if side == "buy":
            return price * (1.0 + self.cost_rate)
        return price * (1.0 - self.cost_rate)

    def buy(self, symbol: str, usd_amount: float, price: float) -> dict:
        if self.mode != "paper":
            logger.error("Blocked a non-paper buy of %s", symbol)
            return {"status": "rejected", "reason": "live trading is disabled", "mode": self.mode}
        if usd_amount <= 0 or price <= 0:
            return {"status": "rejected", "reason": "bad price or size", "mode": "paper"}
        fill = self._fill_price(price, "buy")
        amount = usd_amount / fill
        logger.info(
            "[PAPER] BUY %s | $%.2f signal %.8f fill %.8f after %.0fs delay",
            symbol,
            usd_amount,
            price,
            fill,
            self.delay_seconds,
        )
        return {
            "status": "filled",
            "symbol": symbol,
            "side": "buy",
            "amount": amount,
            "price": fill,
            "signal_price": price,
            "usd_value": usd_amount,
            "mode": "paper",
            "delay_seconds": self.delay_seconds,
        }

    def sell(self, symbol: str, amount: float, price: float) -> dict:
        if self.mode != "paper":
            logger.error("Blocked a non-paper sell of %s", symbol)
            return {"status": "rejected", "reason": "live trading is disabled", "mode": self.mode}
        if amount <= 0 or price < 0:
            return {"status": "rejected", "reason": "bad price or size", "mode": "paper"}
        fill = self._fill_price(price, "sell")
        usd_value = amount * fill
        logger.info(
            "[PAPER] SELL %s | %.4f tokens signal %.8f fill %.8f -> $%.2f",
            symbol,
            amount,
            price,
            fill,
            usd_value,
        )
        return {
            "status": "filled",
            "symbol": symbol,
            "side": "sell",
            "amount": amount,
            "price": fill,
            "signal_price": price,
            "usd_value": usd_value,
            "mode": "paper",
            "delay_seconds": self.delay_seconds,
        }
