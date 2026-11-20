"""Moderate leverage, the short-side test, and the liquidation check."""

from __future__ import annotations

from backtest.ensemble import (
    MAX_GROSS_LEVERAGE,
    EnsembleParams,
    adverse_move_to_liquidation,
    cap_weights,
    model_size,
    simulate_coin,
    simulate_portfolio,
)
from backtest.moderate import _pick_default, _shorts_beat


def test_leverage_only_appears_when_volatility_is_low_and_never_passes_1_5x():
    quiet = EnsembleParams(target_vol=0.25, max_model_weight=5)
    storm = EnsembleParams(target_vol=0.25, max_model_weight=5)
    assert model_size(0.10, quiet) == MAX_GROSS_LEVERAGE
    assert model_size(0.80, storm) < 1.0
    capped = cap_weights({"BTC": 2.0, "ETH": 2.0}, 0.35, 3.0)
    assert max(abs(value) for value in capped.values()) <= 0.35 + 1e-9
    assert sum(abs(value) for value in capped.values()) <= 1.5 + 1e-9


def test_a_downtrend_can_go_short_only_when_shorts_are_allowed():
    close = [200 - i for i in range(80)]
    dates = [f"{i:04d}" for i in range(80)]
    high = [price + 1 for price in close]
    low = [price - 1 for price in close]
    params = EnsembleParams(lookbacks=(5, 10), vol_lookback=10, rebalance_band=0.0, target_vol=0.25)
    blocked = simulate_coin(dates, high, low, close, params, allow_long=[False] * 80, symbol="BTC")
    assert blocked.weight[-1] == 0
    short = simulate_coin(
        dates,
        high,
        low,
        close,
        params,
        allow_long=[False] * 80,
        allow_short=[True] * 80,
        symbol="BTC",
    )
    assert short.weight[-1] < 0
    assert any(trade.side == "short" for trade in short.trades) or short.weight[-1] < 0


def test_liquidation_needs_a_crash_and_a_normal_stop_is_closer():
    move = adverse_move_to_liquidation(1.5)
    assert 0.60 < move < 0.75
    # Breakout at 110, channel midpoint 104. The stop is about 5% away, not 66%.
    assert (110 - 104) / 110 < move

    def series(last_ratio: float):
        close = []
        price = 100.0
        for i in range(50):
            price *= 1.012 if i % 2 == 0 else 1.003
            close.append(price)
        close.append(close[-1] * last_ratio)
        return {
            "date": [f"{i:04d}" for i in range(len(close))],
            "high": [price * 1.001 for price in close],
            "low": [price * 0.999 for price in close[:-1]] + [close[-1]],
            "close": close,
        }

    params = EnsembleParams(
        lookbacks=(5,),
        vol_lookback=10,
        rebalance_band=0.0,
        target_vol=0.25,
        max_model_weight=1.5,
        per_coin_cap=1.5,
        gross_cap=1.5,
    )
    dip = simulate_portfolio({"BTC": series(0.95)}, params, funding_apr=0.0)
    crash = simulate_portfolio({"BTC": series(0.15)}, params, funding_apr=0.0)
    assert dip.liquidations == 0
    assert crash.liquidations >= 1


def test_shorts_stay_off_unless_they_win_both_windows_on_sharpe_and_drawdown():
    long_book = {
        "oos": {"sharpe": 1.5, "max_drawdown_pct": 20},
        "holdout": {"sharpe": 0.4, "max_drawdown_pct": 12},
    }
    worse_drop = {
        "oos": {"sharpe": 1.6, "max_drawdown_pct": 21},
        "holdout": {"sharpe": 0.5, "max_drawdown_pct": 10},
    }
    better = {
        "oos": {"sharpe": 1.6, "max_drawdown_pct": 18},
        "holdout": {"sharpe": 0.5, "max_drawdown_pct": 10},
    }
    assert _shorts_beat(long_book, worse_drop) is False
    assert _shorts_beat(long_book, better) is True


def test_saved_comparison_picks_moderate_long_only_and_never_liquidates():
    from backtest.data import ensure_cache
    from backtest.harness import load_report
    from backtest.moderate import compare

    saved = load_report()["leverage_comparison"]
    fresh = compare(ensure_cache())
    assert saved["default_key"] == fresh["default_key"] == "moderate_long"
    assert saved["shorts_enabled"] is False
    assert saved["capped_gross"] is None
    for key in ("conservative_long", "moderate_15_long", "moderate_15_long_short"):
        for window in ("oos", "holdout"):
            old = saved["rows"][key][window]
            new = fresh["rows"][key][window]
            assert old["sharpe"] == new["sharpe"]
            assert old["max_drawdown_pct"] == new["max_drawdown_pct"]
            assert old["total_return_pct"] == new["total_return_pct"]
            assert old["liquidations"] == 0
            assert old["positive_month_pct"] == new["positive_month_pct"]
            assert old["longest_losing_months"] == new["longest_losing_months"]
    assert _pick_default(
        fresh["rows"]["conservative_long"],
        fresh["rows"]["moderate_15_long"],
        None,
    ) == "moderate_long"


def test_default_uses_annual_return_inside_the_drawdown_cap():
    slow = {"oos": {"sharpe": 2.0, "cagr_pct": 12, "max_drawdown_pct": 10}}
    faster = {"oos": {"sharpe": 1.2, "cagr_pct": 40, "max_drawdown_pct": 29}}
    too_deep = {"oos": {"sharpe": 1.4, "cagr_pct": 70, "max_drawdown_pct": 35}}
    assert _pick_default(slow, faster, too_deep) == "moderate_long"


def test_positive_months_and_the_losing_streak():
    from backtest.metrics import longest_losing_months, positive_month_pct

    dates = ["2020-01-01", "2020-01-02", "2020-02-01", "2020-02-02", "2020-03-01"]
    returns = [0.01, 0.01, -0.02, -0.01, -0.01]
    assert positive_month_pct(dates, returns) == 33.3
    assert longest_losing_months(dates, returns) == 2


def test_calendar_counts_closed_days_and_leaves_empty_months_blank():
    from core.performance import calendar_pnl

    trades = [
        {"bot": "ensemble_trend", "pnl": 10, "closed_at": "2026-01-05T00:00:00"},
        {"bot": "ensemble_trend", "pnl": -4, "closed_at": "2026-01-06T00:00:00"},
        {"bot": "swing", "pnl": -2, "closed_at": "2026-03-01T00:00:00"},
        {"bot": "ensemble_trend", "pnl": 50, "closed_at": "2025-12-01T00:00:00"},
    ]
    report = calendar_pnl(
        trades,
        2026,
        [("ensemble_trend", "Ensemble trend"), ("swing", "Swing")],
    )
    trend = report["rows"][0]
    assert trend["months"][0] == 6
    assert trend["months"][1] is None
    assert trend["ytd"] == 6
    assert trend["win_days_pct"] == 50.0
    assert trend["win_months_pct"] == 100.0
    total = report["rows"][-1]
    assert total["name"] == "Total"
    assert total["ytd"] == 4
    assert total["win_months_pct"] == 50.0


def test_meme_sniper_refuses_leverage():
    from meme_sniper.core.settings import load_sniper_config
    from meme_sniper.core.sniper import MemeSniper

    assert float(load_sniper_config()["leverage"]) == 1
    sniper = MemeSniper({"mode": "paper", "leverage": 3, "capital": {}, "risk": {}, "filters": {}, "execution": {}})
    assert sniper.leverage == 1.0
