"""Long-only Donchian ensemble from Zarattini, Pagani and Barbon.

Nine fixed lookbacks are averaged. Each model enters when the daily close
breaks the prior n-day high, and exits under a stop that starts at the
channel midpoint and only moves up. Position size targets 25% annualized
volatility and never uses leverage. HYPE is never used to choose parameters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


LOOKBACKS = (5, 10, 20, 30, 60, 90, 150, 250, 360)


@dataclass
class EnsembleParams:
    lookbacks: tuple[int, ...] = LOOKBACKS
    target_vol: float = 0.25
    vol_lookback: int = 90
    max_model_weight: float = 1.0
    rebalance_band: float = 0.20
    fee: float = 0.001
    slippage: float = 0.001
    per_coin_cap: float = 0.40
    gross_cap: float = 1.0
    bars_per_year: int = 365

    @property
    def cost(self) -> float:
        return self.fee + self.slippage


@dataclass
class ModelTrade:
    symbol: str
    lookback: int
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float


@dataclass
class CoinResult:
    dates: list[str]
    weight: list[float]
    close: list[float]
    trades: list[ModelTrade] = field(default_factory=list)


def _std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((value - mean) ** 2 for value in values) / (n - 1)
    return math.sqrt(var)


def simulate_coin(
    dates: list[str],
    high: list[float],
    low: list[float],
    close: list[float],
    params: EnsembleParams | None = None,
    allow_long: list[bool] | None = None,
    symbol: str = "",
) -> CoinResult:
    """Daily target weight for one coin, before portfolio caps."""
    params = params or EnsembleParams()
    n = len(close)
    if not (len(dates) == len(high) == len(low) == n):
        raise ValueError("date, high, low and close must be the same length")
    returns = [0.0]
    for i in range(1, n):
        prev = close[i - 1]
        returns.append((close[i] / prev - 1.0) if prev else 0.0)

    models = []
    for lookback in params.lookbacks:
        models.append({"lookback": lookback, "in_pos": False, "stop": 0.0, "entry": 0.0, "entry_date": ""})

    weight = [0.0] * n
    trades: list[ModelTrade] = []
    held = 0.0
    for i in range(n):
        allowed = True if allow_long is None else bool(allow_long[i])
        model_weights = []
        for model in models:
            lookback = model["lookback"]
            if i < lookback:
                model_weights.append(0.0)
                continue
            window_high = max(high[i - lookback : i])
            window_low = min(low[i - lookback : i])
            midpoint = (window_high + window_low) / 2.0
            price = close[i]
            if model["in_pos"]:
                model["stop"] = max(model["stop"], midpoint)
                if price < model["stop"] or not allowed:
                    trades.append(
                        ModelTrade(
                            symbol=symbol,
                            lookback=lookback,
                            entry_date=model["entry_date"],
                            exit_date=dates[i],
                            entry_price=model["entry"],
                            exit_price=price,
                            return_pct=(price / model["entry"] - 1.0) * 100.0 if model["entry"] else 0.0,
                        )
                    )
                    model["in_pos"] = False
            if (not model["in_pos"]) and allowed and price >= window_high:
                model["in_pos"] = True
                model["stop"] = midpoint
                model["entry"] = price
                model["entry_date"] = dates[i]
            if not model["in_pos"]:
                model_weights.append(0.0)
                continue
            vol_start = max(1, i - params.vol_lookback)
            sample = returns[vol_start : i + 1]
            ann_vol = _std(sample) * math.sqrt(params.bars_per_year)
            if ann_vol <= 1e-8:
                model_weights.append(0.0)
            else:
                model_weights.append(min(params.target_vol / ann_vol, params.max_model_weight))
        target = sum(model_weights) / len(params.lookbacks) if params.lookbacks else 0.0
        if held > 0 and target > 0 and abs(target - held) < params.rebalance_band * held:
            target = held
        weight[i] = target
        held = target
    return CoinResult(dates=dates, weight=weight, close=close, trades=trades)


def cap_weights(weights: dict[str, float], per_coin: float, gross: float) -> dict[str, float]:
    capped = {name: min(max(value, 0.0), per_coin) for name, value in weights.items()}
    total = sum(capped.values())
    if total > gross > 0:
        scale = gross / total
        capped = {name: value * scale for name, value in capped.items()}
    return capped


@dataclass
class PortfolioResult:
    dates: list[str]
    equity: list[float]
    daily_returns: list[float]
    trades: list[ModelTrade]
    ending_weights: dict[str, float]


def simulate_portfolio(
    coins: dict[str, dict[str, list]],
    params: EnsembleParams | None = None,
    initial_equity: float = 10_000.0,
    regime: dict[str, bool] | None = None,
    funding_block: dict[str, set[str]] | None = None,
    risk_overlay: bool = False,
) -> PortfolioResult:
    """Combine coins on the union of their dates. Missing coins stay in cash."""
    params = params or EnsembleParams()
    all_dates = sorted({date for frame in coins.values() for date in frame["date"]})
    prepared: dict[str, CoinResult] = {}
    for symbol, frame in coins.items():
        allow = None
        if regime is not None or funding_block is not None:
            allow = []
            blocked = funding_block.get(symbol, set()) if funding_block else set()
            for date in frame["date"]:
                ok = True if regime is None else bool(regime.get(date, False))
                if date in blocked:
                    ok = False
                allow.append(ok)
        prepared[symbol] = simulate_coin(
            frame["date"], frame["high"], frame["low"], frame["close"], params, allow, symbol
        )
    index = {symbol: {date: i for i, date in enumerate(result.dates)} for symbol, result in prepared.items()}
    equity = initial_equity
    curve = []
    daily = []
    prev_w = {symbol: 0.0 for symbol in prepared}
    prev_close = {}
    last_weights = dict(prev_w)
    peak = equity
    for date in all_dates:
        raw = {}
        closes = {}
        for symbol, result in prepared.items():
            pos = index[symbol].get(date)
            if pos is None:
                raw[symbol] = prev_w.get(symbol, 0.0)
                closes[symbol] = prev_close.get(symbol)
                continue
            raw[symbol] = result.weight[pos]
            closes[symbol] = result.close[pos]
        target = cap_weights(raw, params.per_coin_cap, params.gross_cap)
        if risk_overlay and peak > 0:
            drop = (peak - equity) / peak
            if drop >= 0.20:
                target = {symbol: 0.0 for symbol in target}
            elif drop >= 0.10:
                target = {symbol: value * 0.5 for symbol, value in target.items()}
        day_ret = 0.0
        cost = 0.0
        for symbol, weight_now in target.items():
            price = closes.get(symbol)
            old_price = prev_close.get(symbol)
            old_w = prev_w.get(symbol, 0.0)
            if price and old_price and old_w:
                day_ret += old_w * (price / old_price - 1.0)
            cost += abs(weight_now - old_w) * params.cost
        day_ret -= cost
        equity *= 1.0 + day_ret
        peak = max(peak, equity)
        curve.append(equity)
        daily.append(day_ret)
        prev_w = target
        for symbol, price in closes.items():
            if price:
                prev_close[symbol] = price
        last_weights = target
    trades = [trade for result in prepared.values() for trade in result.trades]
    return PortfolioResult(all_dates, curve, daily, trades, last_weights)
