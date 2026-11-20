"""Long-only pullback swing test.

Rules were chosen before the results were read, and they are not changed
to make a window pass. HYPE is in the same coin list as the trend bot, but
it is not used to pick any of these numbers.

Buy when a coin is in an uptrend, Bitcoin is in an uptrend, and a 2-day
RSI is washed out. Sell when that RSI recovers, the daily low hits a stop
two ATRs under the buy, or the trade has been open 12 days.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from backtest.ensemble import cap_weights


@dataclass
class SwingParams:
    sma_trend: int = 100
    rsi_length: int = 2
    rsi_entry: float = 10.0
    rsi_exit: float = 70.0
    atr_length: int = 14
    atr_mult: float = 2.0
    max_hold: int = 12
    target_vol: float = 0.15
    vol_lookback: int = 90
    max_weight: float = 1.0
    fee: float = 0.001
    slippage: float = 0.001
    per_coin_cap: float = 0.25
    gross_cap: float = 0.60
    bars_per_year: int = 365

    @property
    def cost(self) -> float:
        return self.fee + self.slippage


@dataclass
class SwingTrade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    reason: str


@dataclass
class SwingCoinResult:
    dates: list[str]
    weight: list[float]
    close: list[float]
    mark: list[float]
    trades: list[SwingTrade] = field(default_factory=list)


@dataclass
class SwingResult:
    dates: list[str]
    equity: list[float]
    daily_returns: list[float]
    trades: list[SwingTrade]
    ending_weights: dict[str, float]


def _std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (n - 1))


def rsi_wilder(close: list[float], length: int) -> list[float | None]:
    """Wilder RSI. The first value appears on the bar that closes the first window."""
    n = len(close)
    out: list[float | None] = [None] * n
    if length < 1 or n <= length:
        return out
    gains = []
    losses = []
    for i in range(1, length + 1):
        change = close[i] - close[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / length
    avg_loss = sum(losses) / length
    out[length] = _rsi(avg_gain, avg_loss)
    for i in range(length + 1, n):
        change = close[i] - close[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (length - 1) + gain) / length
        avg_loss = (avg_loss * (length - 1) + loss) / length
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def _rsi(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr_wilder(high: list[float], low: list[float], close: list[float], length: int) -> list[float | None]:
    """Wilder ATR. Today's range is known at the close, which is when the signal is read."""
    n = len(close)
    out: list[float | None] = [None] * n
    if length < 1 or n <= length:
        return out
    trs = [0.0]
    for i in range(1, n):
        tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        trs.append(tr)
    avg = sum(trs[1 : length + 1]) / length
    out[length] = avg
    for i in range(length + 1, n):
        avg = (avg * (length - 1) + trs[i]) / length
        out[i] = avg
    return out


def sma(close: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(close)
    if length < 1:
        return out
    rolling = 0.0
    for i, price in enumerate(close):
        rolling += price
        if i >= length:
            rolling -= close[i - length]
        if i >= length - 1:
            out[i] = rolling / length
    return out


def simulate_swing_coin(
    dates: list[str],
    high: list[float],
    low: list[float],
    close: list[float],
    params: SwingParams | None = None,
    allow_long: list[bool] | None = None,
    symbol: str = "",
) -> SwingCoinResult:
    """One coin. A signal at today's close is held starting today, so the stop is checked tomorrow."""
    params = params or SwingParams()
    n = len(close)
    if not (len(dates) == len(high) == len(low) == n):
        raise ValueError("date, high, low and close must be the same length")
    rsi = rsi_wilder(close, params.rsi_length)
    atr = atr_wilder(high, low, close, params.atr_length)
    trend = sma(close, params.sma_trend)
    returns = [0.0]
    for i in range(1, n):
        prev = close[i - 1]
        returns.append((close[i] / prev - 1.0) if prev else 0.0)

    weight = [0.0] * n
    mark = list(close)
    trades: list[SwingTrade] = []
    in_pos = False
    entry_i = 0
    entry_price = 0.0
    stop = 0.0
    size = 0.0
    for i in range(n):
        allowed = True if allow_long is None else bool(allow_long[i])
        if in_pos:
            days = i - entry_i
            stopped = low[i] <= stop
            recovered = rsi[i] is not None and rsi[i] > params.rsi_exit
            timed = days >= params.max_hold
            if stopped or recovered or timed or not allowed:
                if stopped:
                    fill = stop if close[i] >= stop else close[i]
                    reason = "stop"
                elif not allowed:
                    fill = close[i]
                    reason = "regime"
                elif timed:
                    fill = close[i]
                    reason = "time"
                else:
                    fill = close[i]
                    reason = "rsi"
                mark[i] = fill
                trades.append(
                    SwingTrade(
                        symbol=symbol,
                        entry_date=dates[entry_i],
                        exit_date=dates[i],
                        entry_price=entry_price,
                        exit_price=fill,
                        return_pct=(fill / entry_price - 1.0) * 100.0 if entry_price else 0.0,
                        reason=reason,
                    )
                )
                in_pos = False
                weight[i] = 0.0
                continue
            weight[i] = size
            continue
        weight[i] = 0.0
        if not allowed or trend[i] is None or rsi[i] is None or atr[i] is None:
            continue
        if close[i] > trend[i] and rsi[i] < params.rsi_entry and atr[i] > 0:
            vol_start = max(1, i - params.vol_lookback)
            ann_vol = _std(returns[vol_start : i + 1]) * math.sqrt(params.bars_per_year)
            if ann_vol <= 1e-8:
                continue
            in_pos = True
            entry_i = i
            entry_price = close[i]
            stop = entry_price - params.atr_mult * atr[i]
            size = min(params.target_vol / ann_vol, params.max_weight)
            weight[i] = size
    return SwingCoinResult(dates=dates, weight=weight, close=close, mark=mark, trades=trades)


def simulate_swing_portfolio(
    coins: dict[str, dict[str, list]],
    params: SwingParams | None = None,
    initial_equity: float = 10_000.0,
    regime: dict[str, bool] | None = None,
) -> SwingResult:
    """Same cost rule as the trend bot: yesterday's weight earns today's move, and a weight change pays the fee."""
    params = params or SwingParams()
    all_dates = sorted({date for frame in coins.values() for date in frame["date"]})
    prepared: dict[str, SwingCoinResult] = {}
    for symbol, frame in coins.items():
        allow = None
        if regime is not None:
            allow = [bool(regime.get(date, False)) for date in frame["date"]]
        prepared[symbol] = simulate_swing_coin(
            frame["date"], frame["high"], frame["low"], frame["close"], params, allow, symbol
        )
    index = {symbol: {date: i for i, date in enumerate(result.dates)} for symbol, result in prepared.items()}
    equity = initial_equity
    curve = []
    daily = []
    prev_w = {symbol: 0.0 for symbol in prepared}
    prev_mark = {}
    last_weights = dict(prev_w)
    for date in all_dates:
        raw = {}
        marks = {}
        for symbol, result in prepared.items():
            pos = index[symbol].get(date)
            if pos is None:
                raw[symbol] = prev_w.get(symbol, 0.0)
                marks[symbol] = prev_mark.get(symbol)
                continue
            raw[symbol] = result.weight[pos]
            marks[symbol] = result.mark[pos]
        target = cap_weights(raw, params.per_coin_cap, params.gross_cap)
        day_ret = 0.0
        cost = 0.0
        for symbol, weight_now in target.items():
            price = marks.get(symbol)
            old_price = prev_mark.get(symbol)
            old_w = prev_w.get(symbol, 0.0)
            if price and old_price and old_w:
                day_ret += old_w * (price / old_price - 1.0)
            cost += abs(weight_now - old_w) * params.cost
        day_ret -= cost
        equity *= 1.0 + day_ret
        curve.append(equity)
        daily.append(day_ret)
        prev_w = target
        for symbol, price in marks.items():
            if price:
                prev_mark[symbol] = price
        last_weights = target
    trades = [trade for result in prepared.values() for trade in result.trades]
    return SwingResult(all_dates, curve, daily, trades, last_weights)


def score_swing(data: dict) -> dict:
    """Score the fixed rules on the same windows as the trend bot. Does not rewrite those rows."""
    from backtest.harness import HOLDOUT_END, HOLDOUT_START, OOS_WARMUP_YEARS, _btc_benchmark, _coins, _regime_map, _shift_year, _slice_result
    from backtest.metrics import evaluate, verdict

    btc_rows = data["BTCUSDT"]
    oos_start = _shift_year(btc_rows[0]["date"], OOS_WARMUP_YEARS)
    oos_end = "2025-11-19"
    params = SwingParams()
    doubled = replace(params, fee=params.fee * 2, slippage=params.slippage * 2)
    regime = _regime_map(btc_rows)
    coins = _coins(data)

    def run(run_params: SwingParams, start: str, end: str, name: str) -> dict:
        result = simulate_swing_portfolio(coins, run_params, regime=regime)
        _dates, daily, equity = _slice_result(result, start, end)
        trades = [trade for trade in result.trades if start <= trade.exit_date <= end]
        row = evaluate(name, daily, equity, len(trades))
        row["start"] = start
        row["end"] = end
        return row

    oos = run(params, oos_start, oos_end, "Swing, walk-forward out of sample")
    oos_cost = run(doubled, oos_start, oos_end, "Swing at double costs")
    hold = run(params, HOLDOUT_START, HOLDOUT_END, "Swing, holdout Nov 2025 to Nov 2026")
    hold_cost = run(doubled, HOLDOUT_START, HOLDOUT_END, "Swing holdout at double costs")
    btc_oos = _btc_benchmark(btc_rows, oos_start, oos_end, params.cost)
    btc_hold = _btc_benchmark(btc_rows, HOLDOUT_START, HOLDOUT_END, params.cost)
    neighbors = []
    neighbors_ok = True
    for variant in neighbor_params(params):
        label = (
            f"sma {variant.sma_trend}, rsi<{variant.rsi_entry:g}, "
            f"hold {variant.max_hold}, stop {variant.atr_mult:g} ATR"
        )
        row = run(variant, oos_start, oos_end, label)
        net = row.get("total_return_pct")
        if net is None or net <= 0:
            neighbors_ok = False
        neighbors.append(
            {
                "name": label,
                "total_return_pct": row.get("total_return_pct"),
                "sharpe": row.get("sharpe"),
                "max_drawdown_pct": row.get("max_drawdown_pct"),
                "trades": row.get("trades"),
            }
        )

    def pack(row, btc, double_return, neighbors_flag):
        packed = dict(row)
        packed.update(verdict(row, btc, double_return, neighbors_flag))
        return packed

    return {
        "rules": (
            "Buy a coin only when its close is above its 100-day average, Bitcoin's close is above "
            "its 200-day average, and the 2-day RSI is under 10. Sell when the 2-day RSI gets back "
            "above 70, the daily low hits a stop 2 ATRs under the buy, or 12 days have passed. "
            "Size targets 15% annualized volatility, at most 25% in one coin and 60% invested. "
            "Cost is 0.10% fee plus 0.10% slippage per side. The same coins as the trend bot are "
            "loaded, but a coin needs more than 400 daily bars to be scored, so HYPE is not in this test."
        ),
        "strategies": [
            pack(oos, btc_oos, oos_cost.get("total_return_pct"), neighbors_ok),
            pack(hold, btc_hold, hold_cost.get("total_return_pct"), None),
        ],
        "neighbors": neighbors,
        "neighbors_profitable": neighbors_ok,
        "btc": {"oos": btc_oos, "holdout": btc_hold},
    }


def neighbor_params(base: SwingParams | None = None) -> list[SwingParams]:
    """A small grid around the fixed rules. Chosen before any result was read."""
    base = base or SwingParams()
    return [
        replace(base, rsi_entry=8.0),
        replace(base, rsi_entry=15.0),
        replace(base, sma_trend=50),
        replace(base, sma_trend=150),
        replace(base, max_hold=10),
        replace(base, max_hold=15),
        replace(base, atr_mult=1.5),
        replace(base, atr_mult=2.5),
    ]
