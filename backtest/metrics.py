"""Performance numbers for a daily paper backtest. No results are invented."""

from __future__ import annotations

import math
from typing import Any


def sharpe_ratio(daily_returns: list[float], bars_per_year: int = 365) -> float | None:
    if len(daily_returns) < 2:
        return None
    mean = sum(daily_returns) / len(daily_returns)
    var = sum((value - mean) ** 2 for value in daily_returns) / (len(daily_returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return None
    return mean / std * math.sqrt(bars_per_year)


def max_drawdown_pct(equity: list[float]) -> float | None:
    if not equity:
        return None
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst * 100.0


def profit_factor(daily_returns: list[float]) -> float | None:
    gains = sum(value for value in daily_returns if value > 0)
    losses = -sum(value for value in daily_returns if value < 0)
    if losses == 0:
        return None if gains == 0 else float("inf")
    return gains / losses


def total_return_pct(equity: list[float], initial: float) -> float | None:
    if not equity or initial == 0:
        return None
    return (equity[-1] / initial - 1.0) * 100.0


def buy_and_hold(closes: list[float], cost: float = 0.002) -> tuple[list[float], list[float]]:
    """One buy at the first close, then hold. cost is the entry fee plus slippage."""
    if len(closes) < 2 or closes[0] == 0:
        return [], []
    equity = []
    daily = []
    value = 1.0 - cost
    equity.append(value)
    daily.append(-cost)
    for prev, price in zip(closes, closes[1:]):
        change = (price / prev - 1.0) if prev else 0.0
        value *= 1.0 + change
        equity.append(value)
        daily.append(change)
    return daily, [item * 10_000.0 for item in equity]


def evaluate(name: str, daily_returns: list[float], equity: list[float], trades: int, initial: float = 10_000.0) -> dict[str, Any]:
    sharpe = sharpe_ratio(daily_returns)
    factor = profit_factor(daily_returns)
    drawdown = max_drawdown_pct(equity)
    net = total_return_pct(equity, initial)
    return {
        "name": name,
        "sharpe": None if sharpe is None else round(sharpe, 3),
        "profit_factor": None if factor is None or math.isinf(factor) else round(factor, 3),
        "max_drawdown_pct": None if drawdown is None else round(drawdown, 2),
        "trades": int(trades),
        "total_return_pct": None if net is None else round(net, 2),
        "days": len(daily_returns),
    }


def verdict(row: dict[str, Any], btc: dict[str, Any], double_cost_return: float | None, neighbors_ok: bool | None) -> dict[str, Any]:
    """Score one result. A missing number is a fail, not a pass."""
    failed = []
    sharpe = row.get("sharpe")
    factor = row.get("profit_factor")
    drawdown = row.get("max_drawdown_pct")
    trades = row.get("trades") or 0
    if sharpe is None or sharpe < 0.8:
        failed.append("Sharpe below 0.8")
    if factor is None or factor < 1.3:
        failed.append("profit factor below 1.3")
    if drawdown is None or drawdown > 25:
        failed.append("max drawdown deeper than 25%")
    if trades < 100:
        failed.append("fewer than 100 trades")
    if double_cost_return is None or double_cost_return <= 0:
        failed.append("not profitable at double costs")
    beats = False
    if btc.get("sharpe") is not None and sharpe is not None and sharpe > btc["sharpe"]:
        beats = True
    if btc.get("max_drawdown_pct") is not None and drawdown is not None and drawdown < btc["max_drawdown_pct"]:
        beats = True
    if not beats:
        failed.append("does not beat buy-and-hold BTC on Sharpe or max drawdown")
    if neighbors_ok is False:
        failed.append("a nearby parameter set lost money")
    return {
        "pass": not failed,
        "verdict": "PASS" if not failed else "FAIL",
        "failed": failed,
        "beats_buy_and_hold": beats,
        "neighbors_profitable": neighbors_ok,
        "double_cost_return_pct": None if double_cost_return is None else round(double_cost_return, 2),
        "btc_sharpe": btc.get("sharpe"),
        "btc_max_drawdown_pct": btc.get("max_drawdown_pct"),
    }
