"""Synthetic checks for the ensemble, the risk brakes, and the pass/fail score."""

from __future__ import annotations

from backtest.ensemble import EnsembleParams, cap_weights, simulate_coin, simulate_portfolio
from backtest.metrics import verdict
from core.portfolio_guard import PortfolioGuard


def _trend(n=400, step=1.0):
    close = [100 + i * step for i in range(n)]
    return {
        "date": [f"2020-01-{(i % 28) + 1:02d}" for i in range(n)],
        "high": [price + 1 for price in close],
        "low": [price - 1 for price in close],
        "close": close,
    }


def test_ensemble_buys_an_uptrend_and_skips_a_downtrend():
    params = EnsembleParams(lookbacks=(5, 10, 20), vol_lookback=20, rebalance_band=0.0)
    up = _trend(120, 1)
    # Unique dates so the portfolio loop is ordered.
    up["date"] = [f"{i:04d}" for i in range(120)]
    result = simulate_coin(up["date"], up["high"], up["low"], up["close"], params, symbol="BTC")
    assert result.weight[-1] > 0
    assert result.trades == [] or result.weight[-1] > 0

    down_close = [200 - i for i in range(120)]
    down = simulate_coin(
        [f"{i:04d}" for i in range(120)],
        [price + 1 for price in down_close],
        [price - 1 for price in down_close],
        down_close,
        params,
        symbol="BTC",
    )
    assert down.weight[-1] == 0


def test_caps_keep_one_coin_at_40_percent_and_gross_at_100():
    capped = cap_weights({"BTC": 0.8, "ETH": 0.8, "SOL": 0.5}, 0.40, 1.0)
    assert max(capped.values()) <= 0.40 + 1e-9
    assert sum(capped.values()) <= 1.0 + 1e-9


def test_guard_halves_then_goes_to_cash_and_pauses_a_bot():
    guard = PortfolioGuard()
    guard.update_portfolio(100)
    guard.update_portfolio(89)
    assert guard.state == "halved"
    assert guard.exposure_scale() == 0.5
    guard.update_portfolio(79)
    assert guard.state == "cash"
    assert guard.exposure_scale() == 0.0
    guard.update_bot("ensemble_trend", 100)
    guard.update_bot("ensemble_trend", 84)
    assert "ensemble_trend" in guard.paused_bots
    assert guard.exposure_scale("ensemble_trend") == 0.0


def test_verdict_fails_a_weak_result_and_passes_a_strong_one():
    weak = verdict(
        {"sharpe": 0.2, "profit_factor": 1.0, "max_drawdown_pct": 40, "trades": 10},
        {"sharpe": 0.9, "max_drawdown_pct": 50},
        double_cost_return=-5,
        neighbors_ok=False,
    )
    assert weak["verdict"] == "FAIL"
    strong = verdict(
        {"sharpe": 1.1, "profit_factor": 1.5, "max_drawdown_pct": 18, "trades": 120},
        {"sharpe": 0.7, "max_drawdown_pct": 55},
        double_cost_return=4,
        neighbors_ok=True,
    )
    assert strong["verdict"] == "PASS"


def test_portfolio_simulation_stays_long_only():
    btc = _trend(80, 0.5)
    btc["date"] = [f"{i:04d}" for i in range(80)]
    eth_close = [50 + i * 0.2 for i in range(80)]
    eth = {
        "date": btc["date"],
        "high": [price + 0.5 for price in eth_close],
        "low": [price - 0.5 for price in eth_close],
        "close": eth_close,
    }
    params = EnsembleParams(lookbacks=(5, 10), vol_lookback=10, rebalance_band=0.0)
    result = simulate_portfolio({"BTC": btc, "ETH": eth}, params)
    assert result.equity[-1] > 0
    assert all(value >= -1e-9 for value in result.ending_weights.values())
    assert sum(result.ending_weights.values()) <= 1.0 + 1e-6
