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
# A config file cannot ask for more than this. 1.5 means $1.50 of coins per $1 of equity.
MAX_GROSS_LEVERAGE = 1.5
MAINTENANCE_MARGIN = 0.01


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
    # 1.0 means a short is the same size as a long. 0.5 means half.
    short_scale: float = 1.0

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
    side: str = "long"


@dataclass
class CoinResult:
    dates: list[str]
    weight: list[float]
    close: list[float]
    high: list[float] = field(default_factory=list)
    low: list[float] = field(default_factory=list)
    trades: list[ModelTrade] = field(default_factory=list)


def _std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((value - mean) ** 2 for value in values) / (n - 1)
    return math.sqrt(var)


def model_size(ann_vol: float, params: EnsembleParams) -> float:
    """Volatility targeting. High volatility is capped at 1x. Low volatility can use leverage, never past 1.5x."""
    if ann_vol <= 1e-8:
        return 0.0
    size = params.target_vol / ann_vol
    cap = min(params.max_model_weight, MAX_GROSS_LEVERAGE)
    if ann_vol > params.target_vol:
        cap = min(cap, 1.0)
    return min(size, cap)


def adverse_move_to_liquidation(weight: float, maintenance: float = MAINTENANCE_MARGIN) -> float:
    """Fraction the price must move against the position before equity is only maintenance margin.

    At 1.5x and a 1% maintenance margin this is about a 66% move, which is far past the channel stop.
    """
    w = abs(weight)
    if w <= 0 or maintenance >= 1:
        return 1.0
    if w * maintenance >= 1:
        return 0.0
    return (1.0 - maintenance * w) / (w * (1.0 - maintenance))


def simulate_coin(
    dates: list[str],
    high: list[float],
    low: list[float],
    close: list[float],
    params: EnsembleParams | None = None,
    allow_long: list[bool] | None = None,
    symbol: str = "",
    allow_short: list[bool] | None = None,
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
        models.append({"lookback": lookback, "side": 0, "stop": 0.0, "entry": 0.0, "entry_date": ""})

    weight = [0.0] * n
    trades: list[ModelTrade] = []
    held = 0.0
    for i in range(n):
        long_ok = True if allow_long is None else bool(allow_long[i])
        short_ok = False if allow_short is None else bool(allow_short[i])
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
            side = model["side"]
            if side == 1:
                model["stop"] = max(model["stop"], midpoint)
                if price < model["stop"] or not long_ok:
                    _record_exit(trades, symbol, model, price, dates[i])
                    model["side"] = 0
            elif side == -1:
                model["stop"] = min(model["stop"], midpoint)
                if price > model["stop"] or not short_ok:
                    _record_exit(trades, symbol, model, price, dates[i])
                    model["side"] = 0
            if model["side"] == 0:
                if long_ok and price >= window_high:
                    model["side"] = 1
                    model["stop"] = midpoint
                    model["entry"] = price
                    model["entry_date"] = dates[i]
                elif short_ok and price <= window_low:
                    model["side"] = -1
                    model["stop"] = midpoint
                    model["entry"] = price
                    model["entry_date"] = dates[i]
            if model["side"] == 0:
                model_weights.append(0.0)
                continue
            vol_start = max(1, i - params.vol_lookback)
            ann_vol = _std(returns[vol_start : i + 1]) * math.sqrt(params.bars_per_year)
            size = model_size(ann_vol, params)
            if model["side"] < 0:
                size *= min(1.0, max(0.0, params.short_scale))
            model_weights.append(size * model["side"])
        target = sum(model_weights) / len(params.lookbacks) if params.lookbacks else 0.0
        same_way = held * target > 0
        if same_way and abs(target - held) < params.rebalance_band * abs(held):
            target = held
        weight[i] = target
        held = target
    return CoinResult(dates=dates, weight=weight, close=close, high=list(high), low=list(low), trades=trades)


def _record_exit(trades: list[ModelTrade], symbol: str, model: dict, price: float, date: str) -> None:
    entry = model["entry"]
    if model["side"] == -1:
        pct = (entry / price - 1.0) * 100.0 if price else 0.0
        side = "short"
    else:
        pct = (price / entry - 1.0) * 100.0 if entry else 0.0
        side = "long"
    trades.append(
        ModelTrade(
            symbol=symbol,
            lookback=model["lookback"],
            entry_date=model["entry_date"],
            exit_date=date,
            entry_price=entry,
            exit_price=price,
            return_pct=pct,
            side=side,
        )
    )


def cap_weights(weights: dict[str, float], per_coin: float, gross: float, short_scale: float = 1.0) -> dict[str, float]:
    """Clip each coin, then scale the gross book. Gross leverage cannot exceed 1.5x.

    A short uses short_scale times the long coin cap. The gross cap still covers longs and shorts together.
    """
    gross = min(float(gross), MAX_GROSS_LEVERAGE)
    per_coin = min(abs(float(per_coin)), MAX_GROSS_LEVERAGE)
    short_cap = per_coin * min(1.0, max(0.0, float(short_scale)))
    capped = {}
    for name, value in weights.items():
        value = float(value)
        if value < 0:
            capped[name] = max(value, -short_cap)
        else:
            capped[name] = min(value, per_coin)
    total = sum(abs(value) for value in capped.values())
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
    liquidations: int = 0


def simulate_portfolio(
    coins: dict[str, dict[str, list]],
    params: EnsembleParams | None = None,
    initial_equity: float = 10_000.0,
    regime: dict[str, bool] | None = None,
    funding_block: dict[str, set[str]] | None = None,
    risk_overlay: bool = False,
    short_regime: dict[str, bool] | None = None,
    funding_apr: float = 0.10,
    maintenance_margin: float = MAINTENANCE_MARGIN,
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
        short_allow = None
        if short_regime is not None:
            short_allow = [bool(short_regime.get(date, False)) for date in frame["date"]]
        prepared[symbol] = simulate_coin(
            frame["date"],
            frame["high"],
            frame["low"],
            frame["close"],
            params,
            allow,
            symbol,
            short_allow,
        )
    index = {symbol: {date: i for i, date in enumerate(result.dates)} for symbol, result in prepared.items()}
    equity = initial_equity
    curve = []
    daily = []
    prev_w = {symbol: 0.0 for symbol in prepared}
    prev_close = {}
    last_weights = dict(prev_w)
    peak = equity
    liquidations = 0
    for date in all_dates:
        raw = {}
        closes = {}
        highs = {}
        lows = {}
        for symbol, result in prepared.items():
            pos = index[symbol].get(date)
            if pos is None:
                raw[symbol] = prev_w.get(symbol, 0.0)
                closes[symbol] = prev_close.get(symbol)
                continue
            raw[symbol] = result.weight[pos]
            closes[symbol] = result.close[pos]
            highs[symbol] = result.high[pos]
            lows[symbol] = result.low[pos]
        target = cap_weights(raw, params.per_coin_cap, params.gross_cap, params.short_scale)
        if risk_overlay and peak > 0:
            drop = (peak - equity) / peak
            if drop >= 0.20:
                target = {symbol: 0.0 for symbol in target}
            elif drop >= 0.10:
                target = {symbol: value * 0.5 for symbol, value in target.items()}
        price_ret = 0.0
        worst = 0.0
        gross_worst = 0.0
        cost = 0.0
        for symbol, weight_now in target.items():
            price = closes.get(symbol)
            old_price = prev_close.get(symbol)
            old_w = prev_w.get(symbol, 0.0)
            if price and old_price and old_w:
                price_ret += old_w * (price / old_price - 1.0)
            if old_price and old_w:
                extreme = lows.get(symbol) if old_w > 0 else highs.get(symbol)
                if extreme:
                    move = extreme / old_price - 1.0
                    worst += old_w * move
                    gross_worst += abs(old_w) * (extreme / old_price)
            cost += abs(weight_now - old_w) * params.cost
        borrowed = max(0.0, sum(value for value in prev_w.values() if value > 0) - 1.0)
        borrowed += sum(-value for value in prev_w.values() if value < 0)
        funding = borrowed * (funding_apr / params.bars_per_year) if funding_apr and borrowed else 0.0
        liquidated = gross_worst > 0 and (1.0 + worst) <= maintenance_margin * gross_worst
        if liquidated:
            liquidations += 1
            day_ret = worst - cost - funding
            target = {symbol: 0.0 for symbol in target}
        else:
            day_ret = price_ret - cost - funding
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
    return PortfolioResult(all_dates, curve, daily, trades, last_weights, liquidations)
