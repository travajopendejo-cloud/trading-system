"""Walk-forward, holdout, double-cost and neighborhood checks.

Parameters are the fixed Zarattini grid. This module does not search for
better numbers, and it does not fit anything on HYPE.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from backtest.data import STARTS, ensure_cache, frame_from_rows
from backtest.ensemble import EnsembleParams, simulate_portfolio
from backtest.metrics import buy_and_hold, evaluate, verdict

HOLDOUT_START = "2025-11-20"
HOLDOUT_END = "2026-11-19"
OOS_WARMUP_YEARS = 3
RESULTS_PATH = Path(__file__).resolve().parent.parent / "results" / "backtest_results.json"
WINDOW_DAYS = 183


def _shift_year(day: str, years: int) -> str:
    moment = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    try:
        return moment.replace(year=moment.year + years).strftime("%Y-%m-%d")
    except ValueError:
        return moment.replace(year=moment.year + years, day=28).strftime("%Y-%m-%d")


def _slice_result(result, start: str, end: str, initial: float = 10_000.0):
    dates = []
    daily = []
    for date, ret in zip(result.dates, result.daily_returns):
        if start <= date <= end:
            dates.append(date)
            daily.append(ret)
    equity = []
    value = initial
    for ret in daily:
        value *= 1.0 + ret
        equity.append(value)
    return dates, daily, equity


def _windows(first: str, last: str) -> list[tuple[str, str]]:
    windows = []
    cursor = first
    while cursor <= last:
        moment = datetime.strptime(cursor, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_stamp = moment.timestamp() + WINDOW_DAYS * 86_400
        end = datetime.fromtimestamp(end_stamp, timezone.utc).strftime("%Y-%m-%d")
        if end > last:
            end = last
        if cursor <= end:
            windows.append((cursor, end))
        if end == last:
            break
        cursor = datetime.fromtimestamp(end_stamp + 86_400, timezone.utc).strftime("%Y-%m-%d")
    return windows


def _coins(data: dict, start: str | None = None, end: str | None = None) -> dict:
    coins = {}
    for symbol, rows in data.items():
        frame = frame_from_rows(rows, start, end)
        if len(frame["date"]) > 400:
            coins[symbol] = frame
    return coins


def _regime_map(btc_rows: list[dict], window: int = 200) -> dict[str, bool]:
    closes = [row["close"] for row in btc_rows]
    dates = [row["date"] for row in btc_rows]
    allowed = {}
    for i, date in enumerate(dates):
        if i < window:
            allowed[date] = False
            continue
        average = sum(closes[i - window : i]) / window
        allowed[date] = closes[i] > average
    return allowed


def _btc_benchmark(rows: list[dict], start: str, end: str, cost: float) -> dict:
    frame = frame_from_rows(rows, start, end)
    daily, equity = buy_and_hold(frame["close"], cost)
    row = evaluate("Buy and hold BTC", daily, equity, trades=1)
    row["start"] = frame["date"][0] if frame["date"] else start
    row["end"] = frame["date"][-1] if frame["date"] else end
    return row


def run_case(data: dict, params: EnsembleParams, start: str, end: str, regime=None, name: str = "Ensemble"):
    result = simulate_portfolio(_coins(data), params, regime=regime)
    _dates, daily, equity = _slice_result(result, start, end)
    trades = [trade for trade in result.trades if start <= trade.exit_date <= end]
    row = evaluate(name, daily, equity, len(trades))
    row["start"] = start
    row["end"] = end
    return row, result


def neighborhood_profitable(data: dict, start: str, end: str) -> bool:
    """Nearby settings must also make money on the same out-of-sample dates."""
    base = EnsembleParams()
    variants = [
        replace(base, target_vol=0.20),
        replace(base, target_vol=0.30),
        replace(base, rebalance_band=0.10),
        replace(base, rebalance_band=0.30),
        replace(base, lookbacks=tuple(item for item in base.lookbacks if item != 5)),
        replace(base, lookbacks=tuple(item for item in base.lookbacks if item != 360)),
    ]
    for params in variants:
        row, _result = run_case(data, params, start, end, name="neighbor")
        net = row.get("total_return_pct")
        if net is None or net <= 0:
            return False
    return True


def build_report(data: dict | None = None) -> dict:
    data = data or ensure_cache()
    btc_rows = data["BTCUSDT"]
    first_bar = btc_rows[0]["date"]
    oos_start = _shift_year(first_bar, OOS_WARMUP_YEARS)
    oos_end = "2025-11-19"
    params = EnsembleParams()
    doubled = replace(params, fee=params.fee * 2, slippage=params.slippage * 2)

    oos_row, _oos = run_case(data, params, oos_start, oos_end, name="Ensemble, walk-forward out of sample")
    cost_row, _cost = run_case(data, doubled, oos_start, oos_end, name="Ensemble at double costs")
    hold_row, _hold = run_case(data, params, HOLDOUT_START, HOLDOUT_END, name="Ensemble, holdout Nov 2025 to Nov 2026")
    hold_cost, _hc = run_case(data, doubled, HOLDOUT_START, HOLDOUT_END, name="Holdout at double costs")
    regime = _regime_map(btc_rows)
    regime_row, _reg = run_case(
        data, params, oos_start, oos_end, regime=regime, name="BTC 200-day filter, walk-forward out of sample"
    )
    regime_cost, _rc = run_case(
        data, doubled, oos_start, oos_end, regime=regime, name="200-day filter at double costs"
    )
    regime_hold, _rh = run_case(
        data, params, HOLDOUT_START, HOLDOUT_END, regime=regime, name="BTC 200-day filter holdout"
    )
    regime_hold_cost, _rhc = run_case(
        data, doubled, HOLDOUT_START, HOLDOUT_END, regime=regime, name="200-day filter holdout at double costs"
    )
    btc_oos = _btc_benchmark(btc_rows, oos_start, oos_end, params.cost)
    btc_hold = _btc_benchmark(btc_rows, HOLDOUT_START, HOLDOUT_END, params.cost)
    neighbors = neighborhood_profitable(data, oos_start, oos_end)
    windows = []
    for start, end in _windows(oos_start, oos_end):
        window_row, _window = run_case(data, params, start, end, name=f"{start} to {end}")
        windows.append(window_row)

    def pack(row, btc, double_return, neighbors_ok):
        score = verdict(row, btc, double_return, neighbors_ok)
        packed = dict(row)
        packed.update(score)
        return packed

    strategies = [
        pack(oos_row, btc_oos, cost_row.get("total_return_pct"), neighbors),
        pack(hold_row, btc_hold, hold_cost.get("total_return_pct"), None),
        pack(regime_row, btc_oos, regime_cost.get("total_return_pct"), None),
        pack(regime_hold, btc_hold, regime_hold_cost.get("total_return_pct"), None),
        {
            "name": "Funding-rate crowding filter",
            "start": oos_start,
            "end": oos_end,
            "sharpe": None,
            "profit_factor": None,
            "max_drawdown_pct": None,
            "trades": 0,
            "total_return_pct": None,
            "pass": False,
            "verdict": "FAIL",
            "failed": ["Binance futures funding history was not reachable, so this filter was not scored"],
            "beats_buy_and_hold": False,
            "neighbors_profitable": None,
            "double_cost_return_pct": None,
            "btc_sharpe": btc_oos.get("sharpe"),
            "btc_max_drawdown_pct": btc_oos.get("max_drawdown_pct"),
            "note": "Tried the public Binance futures funding endpoint. It did not return data from this environment.",
        },
    ]
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "method": (
            "Daily Binance spot candles. BTC and ETH from 2017-08-17, SOL from 2020-08-11, "
            "HYPE from the first daily candle in the download. Fixed 9 Donchian lookbacks, "
            "25% volatility target, no leverage, 0.10% fee plus 0.10% slippage per side. "
            "Walk-forward out-of-sample starts 3 years after the first BTC bar and stops before "
            "the holdout. The holdout is 2025-11-20 through 2026-11-19 and was not used to change settings. "
            "HYPE was not used to choose any parameter."
        ),
        "oos_start": oos_start,
        "oos_end": oos_end,
        "holdout": {"start": HOLDOUT_START, "end": HOLDOUT_END},
        "strategies": strategies,
        "windows": windows,
        "buy_and_hold": {"oos": btc_oos, "holdout": btc_hold},
        "double_cost_oos_return_pct": cost_row.get("total_return_pct"),
        "samples": {symbol: {"start": rows[0]["date"], "end": rows[-1]["date"], "bars": len(rows)} for symbol, rows in data.items() if rows},
    }


def write_report(report: dict, path: Path = RESULTS_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def load_report(path: Path = RESULTS_PATH) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
