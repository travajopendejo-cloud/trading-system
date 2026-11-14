"""Simple but realistic backtesting engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import structlog

from strategies.base import BaseStrategy

logger = structlog.get_logger(__name__)


@dataclass
class Trade:
    symbol: str
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    amount: float = 0.0
    pnl: float = 0.0
    reason: str = ""


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


class BacktestEngine:
    def __init__(
        self,
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.0004,  # 0.04% taker
        slippage_pct: float = 0.0002,
    ) -> None:
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage_pct = slippage_pct

    def run(
        self,
        strategy: BaseStrategy,
        data: dict[str, pd.DataFrame],  # symbol -> OHLCV
        risk_per_trade_pct: float = 1.0,
        stop_loss_atr_mult: float = 1.5,
        take_profit_atr_mult: float = 2.5,
    ) -> BacktestResult:
        capital = self.initial_capital
        equity_curve = []
        trades: list[Trade] = []
        open_trade: Trade | None = None

        # Align on common index (use first symbol as master)
        symbols = list(data.keys())
        if not symbols:
            return BacktestResult()

        master = data[symbols[0]].copy()
        master = master.sort_values("timestamp").reset_index(drop=True)

        for i in range(50, len(master)):
            window = {s: data[s].iloc[: i + 1].copy() for s in symbols}
            # For simplicity we only trade the first symbol in this basic engine
            symbol = symbols[0]
            df = window[symbol]
            signal = strategy.generate_signal(df)
            row = df.iloc[-1]
            price = float(row["close"])
            ts = row["timestamp"]

            # Exit logic
            if open_trade:
                high = float(row["high"])
                low = float(row["low"])
                exit_price = None
                reason = ""

                if open_trade.side == "long":
                    if open_trade.meta.get("sl") and low <= open_trade.meta["sl"]:
                        exit_price = open_trade.meta["sl"]
                        reason = "stop_loss"
                    elif open_trade.meta.get("tp") and high >= open_trade.meta["tp"]:
                        exit_price = open_trade.meta["tp"]
                        reason = "take_profit"
                else:
                    if open_trade.meta.get("sl") and high >= open_trade.meta["sl"]:
                        exit_price = open_trade.meta["sl"]
                        reason = "stop_loss"
                    elif open_trade.meta.get("tp") and low <= open_trade.meta["tp"]:
                        exit_price = open_trade.meta["tp"]
                        reason = "take_profit"

                if exit_price is not None:
                    # Apply slippage & fees
                    slip = exit_price * self.slippage_pct
                    if open_trade.side == "long":
                        exit_price -= slip
                        pnl = (exit_price - open_trade.entry_price) * open_trade.amount
                    else:
                        exit_price += slip
                        pnl = (open_trade.entry_price - exit_price) * open_trade.amount
                    fee = (open_trade.entry_price + exit_price) * open_trade.amount * self.fee_rate
                    pnl -= fee

                    open_trade.exit_time = ts
                    open_trade.exit_price = exit_price
                    open_trade.pnl = pnl
                    open_trade.reason = reason
                    trades.append(open_trade)
                    capital += pnl
                    open_trade = None

            # Entry logic
            if open_trade is None and signal.action in ("buy", "sell"):
                sl = signal.stop_loss
                tp = signal.take_profit
                if sl is None:
                    atr = float(row.get("atr", price * 0.01))
                    sl = price - stop_loss_atr_mult * atr if signal.action == "buy" else price + stop_loss_atr_mult * atr
                if tp is None:
                    atr = float(row.get("atr", price * 0.01))
                    tp = price + take_profit_atr_mult * atr if signal.action == "buy" else price - take_profit_atr_mult * atr

                risk_amount = capital * (risk_per_trade_pct / 100)
                stop_dist = abs(price - sl)
                if stop_dist <= 0:
                    continue
                amount = risk_amount / stop_dist

                # Slippage on entry
                slip = price * self.slippage_pct
                entry_price = price + slip if signal.action == "buy" else price - slip

                open_trade = Trade(
                    symbol=symbol,
                    side="long" if signal.action == "buy" else "short",
                    entry_time=ts,
                    entry_price=entry_price,
                    amount=amount,
                )
                open_trade.meta = {"sl": sl, "tp": tp}  # type: ignore

            equity_curve.append({"timestamp": ts, "equity": capital})

        # Close any remaining trade at last price
        if open_trade:
            last_price = float(master.iloc[-1]["close"])
            if open_trade.side == "long":
                pnl = (last_price - open_trade.entry_price) * open_trade.amount
            else:
                pnl = (open_trade.entry_price - last_price) * open_trade.amount
            open_trade.exit_time = master.iloc[-1]["timestamp"]
            open_trade.exit_price = last_price
            open_trade.pnl = pnl
            open_trade.reason = "end_of_data"
            trades.append(open_trade)
            capital += pnl

        eq_df = pd.DataFrame(equity_curve).set_index("timestamp")["equity"]
        metrics = self._compute_metrics(trades, eq_df)

        return BacktestResult(trades=trades, equity_curve=eq_df, metrics=metrics)

    def _compute_metrics(self, trades: list[Trade], equity: pd.Series) -> dict[str, Any]:
        if not trades:
            return {"total_trades": 0}

        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_return = (equity.iloc[-1] / self.initial_capital - 1) * 100
        peak = equity.cummax()
        dd = (equity - peak) / peak
        max_dd = dd.min() * 100

        returns = equity.pct_change().dropna()
        sharpe = (returns.mean() / returns.std() * np.sqrt(252 * 24)) if returns.std() > 0 else 0  # rough for intraday

        return {
            "total_trades": len(trades),
            "win_rate": len(wins) / len(trades) * 100 if trades else 0,
            "profit_factor": abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float("inf"),
            "total_pnl": sum(pnls),
            "total_return_pct": total_return,
            "max_drawdown_pct": max_dd,
            "sharpe_approx": sharpe,
            "avg_win": np.mean(wins) if wins else 0,
            "avg_loss": np.mean(losses) if losses else 0,
        }
