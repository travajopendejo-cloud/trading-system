"""Risk management for the Meme Sniper."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List


@dataclass
class Position:
    symbol: str
    amount: float
    entry_price: float
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    take_profit_1: float | None = None
    take_profit_2: float | None = None
    stop_loss: float | None = None


class SniperRiskManager:
    def __init__(
        self,
        initial_capital: float = 100.0,
        max_position_usd: float = 10.0,
        max_open_positions: int = 3,
        daily_loss_limit: float = 20.0,
        take_profit_1: float = 0.80,
        take_profit_2: float = 1.60,
        stop_loss: float = 0.35,
    ):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.max_position_usd = max_position_usd
        self.max_open_positions = max_open_positions
        self.daily_loss_limit = daily_loss_limit

        self.take_profit_1 = take_profit_1
        self.take_profit_2 = take_profit_2
        self.stop_loss = stop_loss

        self.positions: Dict[str, Position] = {}
        self.daily_pnl = 0.0
        self.daily_reset = datetime.now(timezone.utc).date()
        self.halted = False
        self.halt_reason = None

    def can_open(self) -> tuple[bool, str]:
        if self.halted:
            return False, self.halt_reason or "Halted"

        if len(self.positions) >= self.max_open_positions:
            return False, "Max open positions reached"

        if self.daily_pnl <= -self.daily_loss_limit:
            self.halted = True
            self.halt_reason = f"Daily loss limit hit: {self.daily_pnl:.2f}"
            return False, self.halt_reason

        return True, "OK"

    def position_size(self) -> float:
        """Returns the USD size for a new position."""
        return min(self.max_position_usd, self.capital * 0.12)

    def open_position(self, symbol: str, amount: float, entry_price: float) -> Position:
        pos = Position(
            symbol=symbol,
            amount=amount,
            entry_price=entry_price,
            take_profit_1=entry_price * (1 + self.take_profit_1),
            take_profit_2=entry_price * (1 + self.take_profit_2),
            stop_loss=entry_price * (1 - self.stop_loss),
        )
        self.positions[symbol] = pos
        return pos

    def close_position(self, symbol: str, exit_price: float) -> float:
        pos = self.positions.pop(symbol, None)
        if not pos:
            return 0.0

        pnl = (exit_price - pos.entry_price) / pos.entry_price * (pos.amount * pos.entry_price)
        self.daily_pnl += pnl
        self.capital += pnl
        return pnl

    def check_exits(self, symbol: str, current_price: float) -> str | None:
        """Returns 'tp1', 'tp2', 'sl' or None"""
        pos = self.positions.get(symbol)
        if not pos:
            return None

        if current_price >= pos.take_profit_2:
            return "tp2"
        if current_price >= pos.take_profit_1:
            return "tp1"
        if current_price <= pos.stop_loss:
            return "sl"
        return None
