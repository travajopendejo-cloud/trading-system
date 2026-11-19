"""Position and loss limits for the paper meme sniper."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Position:
    key: str
    symbol: str
    amount: float
    entry_price: float
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    take_profit_1: float | None = None
    take_profit_2: float | None = None
    stop_loss: float | None = None
    token_address: str = ""
    source: str = ""
    tp1_done: bool = False


@dataclass
class ClosedTrade:
    symbol: str
    amount: float
    entry_price: float
    exit_price: float
    pnl: float
    reason: str
    opened_at: datetime
    closed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""
    token_address: str = ""

    def as_dashboard(self) -> dict:
        return {
            "bot": "meme_sniper",
            "symbol": self.symbol,
            "side": "long",
            "amount": self.amount,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
        }


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
        self.positions: dict[str, Position] = {}
        self.closed_trades: list[ClosedTrade] = []
        self.daily_pnl = 0.0
        self.daily_reset = datetime.now(timezone.utc).date()
        self.halted = False
        self.halt_reason: str | None = None

    def roll_day(self, now: datetime | None = None) -> None:
        today = (now or datetime.now(timezone.utc)).date()
        if today == self.daily_reset:
            return
        self.daily_pnl = 0.0
        self.daily_reset = today
        if self.halted and (self.halt_reason or "").startswith("Daily loss"):
            self.halted = False
            self.halt_reason = None

    def can_open(self) -> tuple[bool, str]:
        self.roll_day()
        if self.halted:
            return False, self.halt_reason or "Halted"
        if len(self.positions) >= self.max_open_positions:
            return False, "Max open positions reached"
        if self.daily_pnl <= -self.daily_loss_limit:
            self.halted = True
            self.halt_reason = f"Daily loss limit hit: {self.daily_pnl:.2f}"
            return False, self.halt_reason
        if self.capital < 1:
            return False, "Not enough paper capital left"
        return True, "OK"

    def position_size(self) -> float:
        """USD size for the next paper buy."""
        sized = min(self.max_position_usd, self.capital * 0.12, self.capital)
        return max(0.0, sized)

    def open_position(
        self,
        key: str,
        symbol: str,
        amount: float,
        entry_price: float,
        token_address: str = "",
        source: str = "",
    ) -> Position | None:
        if key in self.positions or amount <= 0 or entry_price <= 0:
            return None
        cost = amount * entry_price
        if cost > self.capital + 1e-9:
            return None
        self.capital -= cost
        pos = Position(
            key=key,
            symbol=symbol,
            amount=amount,
            entry_price=entry_price,
            take_profit_1=entry_price * (1 + self.take_profit_1),
            take_profit_2=entry_price * (1 + self.take_profit_2),
            stop_loss=entry_price * (1 - self.stop_loss),
            token_address=token_address,
            source=source,
        )
        self.positions[key] = pos
        return pos

    def reduce_position(
        self,
        key: str,
        sell_amount: float,
        exit_price: float,
        reason: str,
        now: datetime | None = None,
    ) -> ClosedTrade | None:
        pos = self.positions.get(key)
        if not pos or sell_amount <= 0 or exit_price < 0:
            return None
        sell_amount = min(sell_amount, pos.amount)
        pnl = (exit_price - pos.entry_price) * sell_amount
        self.capital += sell_amount * exit_price
        self.daily_pnl += pnl
        pos.amount -= sell_amount
        trade = ClosedTrade(
            symbol=pos.symbol,
            amount=sell_amount,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            reason=reason,
            opened_at=pos.entry_time,
            closed_at=now or datetime.now(timezone.utc),
            source=pos.source,
            token_address=pos.token_address,
        )
        self.closed_trades.append(trade)
        if len(self.closed_trades) > 200:
            self.closed_trades = self.closed_trades[-200:]
        if pos.amount <= 1e-9:
            self.positions.pop(key, None)
        self._check_daily_loss()
        return trade

    def _check_daily_loss(self) -> None:
        if self.daily_pnl <= -self.daily_loss_limit:
            self.halted = True
            self.halt_reason = f"Daily loss limit hit: {self.daily_pnl:.2f}"

    def check_exits(self, key: str, current_price: float) -> str | None:
        """Return 'tp1', 'tp2', 'sl', or None. Take-profit 1 fires only once."""
        pos = self.positions.get(key)
        if not pos or current_price <= 0:
            return None
        if pos.stop_loss is not None and current_price <= pos.stop_loss:
            return "sl"
        if pos.take_profit_2 is not None and current_price >= pos.take_profit_2:
            return "tp2"
        if (
            not pos.tp1_done
            and pos.take_profit_1 is not None
            and current_price >= pos.take_profit_1
        ):
            return "tp1"
        return None

    def get_closed_trades(self, limit: int = 15) -> list[ClosedTrade]:
        return list(reversed(self.closed_trades[-limit:]))
