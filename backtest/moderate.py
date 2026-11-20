"""Compare the current conservative book with a moderate 1.5x book.

Binance funding history did not answer from this environment (HTTP 451 and 404).
Borrowed notional is charged 10% a year instead. Longs use borrowed money only
above 1x. Shorts borrow the whole short. Fee and slippage stay 0.10% each side.

The leverage cap walks 1.5x, then 1.25x, then 1.0x, and stops at the highest
cap whose 2020-2025 max drop is 30% or less. The live default is the book
with the highest 2020-2025 annual return among those whose max drop is 30%
or less. Daily win rate is not part of that choice. Shorts are allowed only
when they beat that long-only book on both windows, on Sharpe and on the max drop.
"""

from __future__ import annotations

from dataclasses import replace

from backtest.ensemble import EnsembleParams, simulate_portfolio
from backtest.harness import HOLDOUT_END, HOLDOUT_START, _btc_benchmark, _coins, _regime_map, _shift_year, _slice_result
from backtest.metrics import evaluate, longest_losing_months, positive_month_pct, worst_month_pct

FUNDING_APR = 0.10
OOS_END = "2025-11-19"


def _short_map(long_map: dict[str, bool], btc_rows: list[dict], window: int = 200) -> dict[str, bool]:
    """Shorts are allowed only once 200 closes exist and Bitcoin is under that average."""
    dates = [row["date"] for row in btc_rows]
    ready = {date: index >= window for index, date in enumerate(dates)}
    return {date: bool(ready.get(date)) and not long_map.get(date, False) for date in long_map}


def _params(target_vol: float, per_coin: float, gross: float) -> EnsembleParams:
    return EnsembleParams(
        target_vol=target_vol,
        max_model_weight=min(gross, 1.5),
        per_coin_cap=per_coin,
        gross_cap=gross,
        fee=0.001,
        slippage=0.001,
    )


def run_window(
    data: dict,
    params: EnsembleParams,
    start: str,
    end: str,
    name: str,
    shorts: bool,
    short_scale: float = 1.0,
) -> dict:
    params = replace(params, short_scale=short_scale if shorts else 1.0)
    btc_rows = data["BTCUSDT"]
    long_map = _regime_map(btc_rows)
    short_map = _short_map(long_map, btc_rows) if shorts else None
    result = simulate_portfolio(
        _coins(data),
        params,
        regime=long_map,
        short_regime=short_map,
        funding_apr=FUNDING_APR,
    )
    dates, daily, equity = _slice_result(result, start, end)
    trades = [trade for trade in result.trades if start <= trade.exit_date <= end]
    row = evaluate(name, daily, equity, len(trades))
    row["start"] = start
    row["end"] = end
    row["worst_month_pct"] = None if not dates else round(worst_month_pct(dates, daily) or 0.0, 2)
    row["positive_month_pct"] = None if not dates else round(positive_month_pct(dates, daily) or 0.0, 1)
    row["longest_losing_months"] = 0 if not dates else longest_losing_months(dates, daily)
    row["liquidations"] = int(result.liquidations)
    row["gross_cap"] = params.gross_cap
    row["target_vol"] = params.target_vol
    row["per_coin_cap"] = params.per_coin_cap
    row["shorts"] = shorts
    row["short_scale"] = params.short_scale if shorts else 0.0
    return row


def compare(data: dict) -> dict:
    """Run the fixed cases once. Nothing here searches for a better indicator."""
    oos_start = _shift_year(data["BTCUSDT"][0]["date"], 3)
    windows = (("oos", oos_start, OOS_END), ("holdout", HOLDOUT_START, HOLDOUT_END))
    conservative = _params(0.15, 0.25, 0.60)
    moderate = _params(0.25, 0.35, 1.5)

    def both(params: EnsembleParams, label: str, shorts: bool) -> dict[str, dict]:
        out = {}
        for key, start, end in windows:
            out[key] = run_window(data, params, start, end, label, shorts)
            doubled = replace(params, fee=params.fee * 2, slippage=params.slippage * 2)
            cost = run_window(data, doubled, start, end, label + " at double costs", shorts)
            out[key]["double_cost_return_pct"] = cost["total_return_pct"]
        return out

    rows = {
        "conservative_long": both(conservative, "Conservative long-only", False),
        "moderate_15_long": both(moderate, "Moderate 1.5x long-only", False),
        "moderate_15_long_short": both(moderate, "Moderate 1.5x long/short", True),
    }
    capped_gross = None
    capped = None
    long_oos_dd = rows["moderate_15_long"]["oos"]["max_drawdown_pct"]
    if long_oos_dd is not None and long_oos_dd > 30:
        for gross in (1.25, 1.0):
            trial = _params(0.25, min(0.35, gross), gross)
            trial_rows = both(trial, f"Moderate {gross:.2f}x long-only", False)
            if trial_rows["oos"]["max_drawdown_pct"] is not None and trial_rows["oos"]["max_drawdown_pct"] <= 30:
                capped_gross = gross
                capped = trial_rows
                rows[f"moderate_{gross:.2f}_long"] = trial_rows
                rows[f"moderate_{gross:.2f}_long_short"] = both(
                    trial, f"Moderate {gross:.2f}x long/short", True
                )
                break
            rows[f"moderate_{gross:.2f}_long"] = trial_rows

    long_book = capped or rows["moderate_15_long"]
    short_book = rows.get(f"moderate_{capped_gross:.2f}_long_short") if capped_gross else rows["moderate_15_long_short"]
    shorts_allowed = _shorts_beat(long_book, short_book)
    choice = _pick_default(rows["conservative_long"], long_book, short_book if shorts_allowed else None)
    btc = {
        "oos": _btc_benchmark(data["BTCUSDT"], oos_start, OOS_END, 0.002),
        "holdout": _btc_benchmark(data["BTCUSDT"], HOLDOUT_START, HOLDOUT_END, 0.002),
    }
    return {
        "funding": "10% a year on borrowed notional. Binance funding history returned HTTP 451 and 404.",
        "oos_start": oos_start,
        "oos_end": OOS_END,
        "rows": rows,
        "capped_gross": capped_gross,
        "shorts_enabled": shorts_allowed,
        "default_key": choice,
        "btc": btc,
    }


def _better(left: dict, right: dict) -> bool:
    """True when left beats right on both Sharpe and the size of the drop."""
    if None in (left.get("sharpe"), right.get("sharpe"), left.get("max_drawdown_pct"), right.get("max_drawdown_pct")):
        return False
    return left["sharpe"] > right["sharpe"] and left["max_drawdown_pct"] < right["max_drawdown_pct"]


def _shorts_beat(long_book: dict, short_book: dict) -> bool:
    return _better(short_book["oos"], long_book["oos"]) and _better(short_book["holdout"], long_book["holdout"])


def _pick_default(conservative: dict, moderate_long: dict, moderate_short: dict | None) -> str:
    """Highest 2020-2025 annual return among books whose max drop is 30% or less."""
    candidates = [("conservative_long", conservative)]
    if moderate_long["oos"]["max_drawdown_pct"] is not None and moderate_long["oos"]["max_drawdown_pct"] <= 30:
        candidates.append(("moderate_long", moderate_long))
    if moderate_short is not None and moderate_short["oos"]["max_drawdown_pct"] is not None and moderate_short["oos"]["max_drawdown_pct"] <= 30:
        candidates.append(("moderate_long_short", moderate_short))
    eligible = [item for item in candidates if item[1]["oos"].get("cagr_pct") is not None]
    if not eligible:
        return "conservative_long"
    eligible.sort(key=lambda item: item[1]["oos"]["cagr_pct"], reverse=True)
    return eligible[0][0]


SHORT_SIZE_LADDER = (0.5, 0.33, 0.25)


def _two_windows(data: dict, params: EnsembleParams, label: str, shorts: bool, short_scale: float, oos_start: str) -> dict[str, dict]:
    windows = (("oos", oos_start, OOS_END), ("holdout", HOLDOUT_START, HOLDOUT_END))
    out = {}
    for key, start, end in windows:
        out[key] = run_window(data, params, start, end, label, shorts, short_scale)
        doubled = replace(params, fee=params.fee * 2, slippage=params.slippage * 2)
        cost = run_window(data, doubled, start, end, label + " at double costs", shorts, short_scale)
        out[key]["double_cost_return_pct"] = cost["total_return_pct"]
    return out


def compare_shorts(data: dict) -> dict:
    """Long-only 1.5x against longs plus a smaller short book. Shorts shrink until the 2020-2025 drop is within 30%."""
    oos_start = _shift_year(data["BTCUSDT"][0]["date"], 3)
    moderate = _params(0.25, 0.35, 1.5)
    long_only = _two_windows(data, moderate, "Moderate 1.5x long-only", False, 1.0, oos_start)
    tried = []
    chosen_scale = None
    chosen = None
    for scale in SHORT_SIZE_LADDER:
        book = _two_windows(data, moderate, f"Long plus {scale:.2f}x shorts", True, scale, oos_start)
        tried.append({"scale": scale, "oos_max_drawdown_pct": book["oos"]["max_drawdown_pct"]})
        drop = book["oos"]["max_drawdown_pct"]
        if drop is not None and drop <= 30 and chosen is None:
            chosen_scale = scale
            chosen = book
            break
    return {
        "funding": "10% a year on the whole short, and on long exposure above 1x. Binance funding history returned HTTP 451 and 404.",
        "long_only": long_only,
        "chosen_scale": chosen_scale,
        "chosen": chosen,
        "tried": tried,
        "fits": chosen_scale is not None,
    }
