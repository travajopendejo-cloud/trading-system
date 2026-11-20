"""The swing rules, a paper fill, and the saved score."""

from __future__ import annotations

from backtest.swing import SwingParams, rsi_wilder, simulate_swing_coin, simulate_swing_portfolio


def _climb_then_dip():
    close = []
    price = 100.0
    for _ in range(90):
        price *= 1.004
        close.append(price)
    close.append(close[-1] * 0.992)
    close.append(close[-1] * 0.992)
    return close


def test_rsi_falls_after_down_days():
    rising = rsi_wilder([100 + i for i in range(10)], 2)
    falling = rsi_wilder([100 - i * 3 for i in range(10)], 2)
    assert rising[-1] == 100.0
    assert falling[-1] < 10


def test_dip_in_an_uptrend_buys_and_a_crash_stops_out():
    close = _climb_then_dip()
    params = SwingParams(sma_trend=20, rsi_entry=15, atr_length=10, vol_lookback=20, max_hold=5, target_vol=0.5)
    dates = [f"{i:04d}" for i in range(len(close))]
    high = [price * 1.01 for price in close]
    low = [price * 0.99 for price in close]
    held = simulate_swing_coin(dates, high, low, close, params, symbol="BTC")
    assert held.weight[-1] > 0
    assert held.trades == []

    crash = close[-1] * 0.8
    close.append(crash)
    dates.append(f"{len(close)-1:04d}")
    high.append(close[-2])
    low.append(crash)
    stopped = simulate_swing_coin(dates, high, low, close, params, symbol="BTC")
    assert stopped.trades[-1].reason == "stop"
    assert stopped.weight[-1] == 0


def test_downtrend_does_not_buy():
    close = [200 - i for i in range(80)]
    params = SwingParams(sma_trend=20, rsi_entry=30, atr_length=10, vol_lookback=10)
    dates = [f"{i:04d}" for i in range(len(close))]
    result = simulate_swing_coin(
        dates,
        [price + 1 for price in close],
        [price - 1 for price in close],
        close,
        params,
        symbol="BTC",
    )
    assert result.trades == []
    assert result.weight[-1] == 0


def test_two_coins_stay_inside_the_conservative_caps():
    close = _climb_then_dip()
    frame = {
        "date": [f"{i:04d}" for i in range(len(close))],
        "high": [price * 1.01 for price in close],
        "low": [price * 0.99 for price in close],
        "close": close,
    }
    params = SwingParams(
        sma_trend=20,
        rsi_entry=15,
        atr_length=10,
        vol_lookback=20,
        target_vol=0.9,
        per_coin_cap=0.25,
        gross_cap=0.60,
    )
    result = simulate_swing_portfolio({"BTC": frame, "ETH": frame}, params)
    assert all(0 <= weight <= 0.25 + 1e-9 for weight in result.ending_weights.values())
    assert sum(result.ending_weights.values()) <= 0.60 + 1e-9


def test_swing_paper_trade_is_once_per_day_and_stays_on_paper():
    from bots.swing_bot import FORWARD_TEST_LABEL, SwingBot

    class Config:
        name = "swing"
        parameters = {
            "sma_trend": 20,
            "rsi_entry": 15,
            "atr_length": 10,
            "vol_lookback": 20,
            "max_hold": 12,
            "target_vol": 0.5,
            "fee_rate": 0.001,
            "slippage_rate": 0.001,
            "per_coin_cap": 0.25,
            "gross_cap": 0.60,
        }

    close = _climb_then_dip()
    # Keep the coin above a 200-day average by using a short history and a rising path.
    # The regime filter needs 200 bars, so extend the climb first.
    long_close = []
    price = 50.0
    for _ in range(210):
        price *= 1.002
        long_close.append(price)
    long_close.append(long_close[-1] * 0.992)
    long_close.append(long_close[-1] * 0.992)
    frame = {
        "date": [f"{i:04d}" for i in range(len(long_close))],
        "high": [price * 1.01 for price in long_close],
        "low": [price * 0.99 for price in long_close],
        "close": long_close,
    }
    bot = SwingBot(Config(), allocated_equity=3000, trading_mode="live")
    assert bot.trading_mode == "paper"
    assert FORWARD_TEST_LABEL in bot.status_note
    orders = bot.run_daily_decision({"BTCUSDT": frame}, frame["date"][-1])
    assert orders
    assert orders[0]["side"] == "buy"
    assert orders[0]["mode"] == "paper"
    assert orders[0]["price"] > orders[0]["signal_price"]
    assert bot.run_daily_decision({"BTCUSDT": frame}, frame["date"][-1]) == []


def test_saved_swing_score_matches_a_fresh_run():
    from backtest.data import ensure_cache
    from backtest.harness import load_report
    from backtest.swing import score_swing

    saved = load_report()
    fresh = score_swing(ensure_cache())
    saved_rows = [row for row in saved["strategies"] if str(row["name"]).startswith("Swing")]
    assert [row["name"] for row in saved_rows] == [row["name"] for row in fresh["strategies"]]
    for old, new in zip(saved_rows, fresh["strategies"]):
        assert old["verdict"] == new["verdict"] == "FAIL"
        assert old["sharpe"] == new["sharpe"]
        assert old["profit_factor"] == new["profit_factor"]
        assert old["max_drawdown_pct"] == new["max_drawdown_pct"]
        assert old["trades"] == new["trades"]
        assert old["total_return_pct"] == new["total_return_pct"]
    assert saved["swing"]["neighbors_profitable"] is False
