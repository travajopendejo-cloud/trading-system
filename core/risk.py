"""Risk management – both per-bot and portfolio level."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Position:
    symbol: str
    side: str  # long | short
    amount: float
    entry_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    bot_name: str = ""
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ClosedTrade:
    bot_name: str
    symbol: str
    side: str
    amount: float
    entry_price: float
    exit_price: float
    pnl: float
    opened_at: datetime
    closed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RiskManager:
    def __init__(
        self,
        initial_equity: float,
        max_drawdown_pct: float = 15.0,
        daily_loss_limit_pct: float = 5.0,
        max_total_exposure_pct: float = 80.0,
        max_open_positions: int = 12,
    ) -> None:
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.peak_equity = initial_equity
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_open_positions = max_open_positions

        self.positions: Dict[str, Position] = {}
        self.closed_trades: List[ClosedTrade] = []   # ← NEW
        self.daily_pnl = 0.0
        self.daily_reset_date = datetime.now(timezone.utc).date()
        self.halted = False
        self.halt_reason: str | None = None

    def update_equity(self, equity: float) -> None:
        self.equity = equity
        if equity > self.peak_equity:
            self.peak_equity = equity
        self._check_drawdown()
        self._check_daily_loss()

    def _check_drawdown(self) -> None:
        if self.peak_equity <= 0:
            return
        dd = (self.peak_equity - self.equity) / self.peak_equity * 100
        if dd >= self.max_drawdown_pct:
            self.halted = True
            self.halt_reason = f"Max drawdown reached: {dd:.2f}%"
            logger.warning("risk_halt", reason=self.halt_reason)

    def _check_daily_loss(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.daily_reset_date:
            self.daily_pnl = 0.0
            self.daily_reset_date = today
        loss_pct = -self.daily_pnl / self.initial_equity * 100 if self.daily_pnl < 0 else 0
        if loss_pct >= self.daily_loss_limit_pct:
            self.halted = True
            self.halt_reason = f"Daily loss limit reached: {loss_pct:.2f}%"
            logger.warning("risk_halt", reason=self.halt_reason)

    def record_realized_pnl(self, pnl: float) -> None:
        self.daily_pnl += pnl
        self.equity += pnl
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        self._check_drawdown()
        self._check_daily_loss()

    def can_open_position(self, bot_name: str, notional: float) -> tuple[bool, str]:
        if self.halted:
            return False, self.halt_reason or "System halted"
        if len(self.positions) >= self.max_open_positions:
            return False, "Max open positions reached"
        current_exposure = sum(p.amount * p.entry_price for p in self.positions.values())
        new_exposure = current_exposure + notional
        max_exposure = self.equity * (self.max_total_exposure_pct / 100)
        if new_exposure > max_exposure:
            return False, f"Max exposure exceeded ({new_exposure:.0f} > {max_exposure:.0f})"
        return True, "OK"

    def calculate_position_size(
        self,
        equity: float,
        risk_pct: float,
        entry_price: float,
        stop_price: float,
    ) -> float:
        if entry_price <= 0 or stop_price <= 0:
            return 0.0
        risk_amount = equity * (risk_pct / 100)
        stop_distance = abs(entry_price - stop_price)
        if stop_distance == 0:
            return 0.0
        return risk_amount / stop_distance

    def add_position(self, pos: Position) -> None:
        key = f"{pos.bot_name}:{pos.symbol}"
        self.positions[key] = pos

    def close_position(
        self,
        bot_name: str,
        symbol: str,
        exit_price: float,
        pnl: float,
    ) -> Position | None:
        """Close a position and record it in trade history."""
        key = f"{bot_name}:{symbol}"
        pos = self.positions.pop(key, None)
        if pos is None:
            return None

        trade = ClosedTrade(
            bot_name=pos.bot_name,
            symbol=pos.symbol,
            side=pos.side,
            amount=pos.amount,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            opened_at=pos.opened_at,
        )
        self.closed_trades.append(trade)

        # Keep only the last 50 trades to avoid memory growth
        if len(self.closed_trades) > 50:
            self.closed_trades = self.closed_trades[-50:]

        self.record_realized_pnl(pnl)
        return pos

    def remove_position(self, bot_name: str, symbol: str) -> Position | None:
        # Keep old method for compatibility, but prefer close_position
        key = f"{bot_name}:{symbol}"
        return self.positions.pop(key, None)

    def get_positions(self, bot_name: str | None = None) -> list[Position]:
        if bot_name is None:
            return list(self.positions.values())
        return [p for p in self.positions.values() if p.bot_name == bot_name]

    def get_closed_trades(self, limit: int = 20) -> list[ClosedTrade]:
        return list(reversed(self.closed_trades[-limit:]))

    @property
    def current_drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.equity) / self.peak_equity * 100
