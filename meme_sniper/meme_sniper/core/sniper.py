"""Main sniper logic."""

from __future__ import annotations

import logging
from typing import Any

from .risk import SniperRiskManager, Position
from .filters import TokenFilter, TokenInfo
from .executor import Executor

logger = logging.getLogger(__name__)


class MemeSniper:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        capital_cfg = config.get("capital", {})
        risk_cfg = config.get("risk", {})

        self.risk = SniperRiskManager(
            initial_capital=float(capital_cfg.get("initial", 100)),
            max_position_usd=float(capital_cfg.get("max_position_usd", 10)),
            max_open_positions=int(capital_cfg.get("max_open_positions", 3)),
            daily_loss_limit=float(capital_cfg.get("daily_loss_limit_usd", 20)),
            take_profit_1=float(risk_cfg.get("take_profit_1", 0.80)),
            take_profit_2=float(risk_cfg.get("take_profit_2", 1.60)),
            stop_loss=float(risk_cfg.get("stop_loss", 0.35)),
        )

        self.filters = TokenFilter(config)
        self.executor = Executor(mode=config.get("mode", "paper"))
        self.time_stop_minutes = float(risk_cfg.get("time_stop_minutes", 12))

    def evaluate_token(self, token: TokenInfo) -> tuple[bool, str]:
        """Check if a token passes filters and risk rules."""
        can_open, reason = self.risk.can_open()
        if not can_open:
            return False, reason

        return self.filters.is_valid(token)

    def open_trade(self, token: TokenInfo) -> Position | None:
        """Try to open a new position."""
        ok, reason = self.evaluate_token(token)
        if not ok:
            logger.info(f"Skipped {token.symbol}: {reason}")
            return None

        usd_size = self.risk.position_size()
        order = self.executor.buy(token.symbol, usd_size, token.price)

        if order["status"] != "filled":
            logger.warning(f"Buy failed for {token.symbol}")
            return None

        pos = self.risk.open_position(
            symbol=token.symbol,
            amount=order["amount"],
            entry_price=order["price"],
        )
        logger.info(f"Opened position: {token.symbol} | size ${usd_size:.2f}")
        return pos

    def check_position(self, symbol: str, current_price: float) -> None:
        """Check if an open position should be closed."""
        signal = self.risk.check_exits(symbol, current_price)
        if not signal:
            return

        pos = self.risk.positions.get(symbol)
        if not pos:
            return

        if signal == "tp1":
            # Sell 50%
            sell_amount = pos.amount * 0.5
            self.executor.sell(symbol, sell_amount, current_price)
            pos.amount -= sell_amount
            logger.info(f"TP1 hit on {symbol} – sold 50%")
        else:
            # Full exit (tp2 or stop loss)
            self.executor.sell(symbol, pos.amount, current_price)
            pnl = self.risk.close_position(symbol, current_price)
            logger.info(f"{signal.upper()} on {symbol} | PnL: ${pnl:.2f}")
