"""Performance stats, portfolio split, and public candle parsing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from bots.base_bot import trail_stop
from core.allocation import clamp_sniper_share, portfolio_allocations, scale_sniper_config
from core.config import load_bot_config, load_global_config
from core.performance import summarize
from core.public_klines import parse_binance_klines, spot_symbol
from strategies.breakout import BreakoutStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend import TrendStrategy


def test_sniper_share_stays_between_10_and_30_percent():
    assert clamp_sniper_share(0.20) == 0.20
    assert clamp_sniper_share(20) == 0.20
    assert clamp_sniper_share("20%") == 0.20
    assert clamp_sniper_share(0.05) == 0.10
    assert clamp_sniper_share(0.50) == 0.30
    assert clamp_sniper_share("nope") == 0.20


def test_other_bots_share_what_the_sniper_does_not_take():
    rows = portfolio_allocations(
        10_000,
        {"trend_bot": 0.35, "mean_reversion_bot": 0.35, "breakout_bot": 0.30},
        sniper_share=0.20,
        sniper_enabled=True,
    )
    by_name = {row["key"]: row for row in rows}
    assert abs(by_name["meme_sniper"]["usd"] - 2000) < 0.01
    assert abs(by_name["trend_bot"]["usd"] - 2800) < 0.01
    assert abs(by_name["mean_reversion_bot"]["usd"] - 2800) < 0.01
    assert abs(by_name["breakout_bot"]["usd"] - 2400) < 0.01
    assert abs(sum(row["usd"] for row in rows) - 10_000) < 0.01

    full = portfolio_allocations(
        10_000,
        {"trend_bot": 0.35, "mean_reversion_bot": 0.35, "breakout_bot": 0.30},
        sniper_enabled=False,
    )
    assert "meme_sniper" not in {row["key"] for row in full}
    assert abs(sum(row["usd"] for row in full) - 10_000) < 0.01


def test_sniper_limits_scale_with_its_slice_and_stay_paper():
    scaled = scale_sniper_config(
        {
            "mode": "live",
            "capital": {
                "initial": 100,
                "max_position_usd": 10,
                "max_open_positions": 3,
                "daily_loss_limit_usd": 20,
            },
        },
        allocated_usd=2000,
        share=0.20,
    )
    assert scaled["mode"] == "paper"
    assert scaled["capital"]["initial"] == 2000
    assert scaled["capital"]["max_position_usd"] == 200
    assert scaled["capital"]["daily_loss_limit_usd"] == 400
    assert scaled["capital"]["max_open_positions"] == 3

    small = scale_sniper_config(
        {"capital": {"initial": 100, "max_position_usd": 10, "max_open_positions": 3, "daily_loss_limit_usd": 20}},
        allocated_usd=1000,
        share=0.10,
    )
    assert small["capital"]["max_position_usd"] == 100
    assert small["capital"]["daily_loss_limit_usd"] == 200
    assert small["capital"]["max_open_positions"] == 2

    large = scale_sniper_config(
        {"capital": {"initial": 100, "max_position_usd": 10, "max_open_positions": 3, "daily_loss_limit_usd": 20}},
        allocated_usd=3000,
        share=0.30,
    )
    assert large["capital"]["max_open_positions"] == 5
    assert large["capital"]["max_position_usd"] == 300


def test_performance_math_from_closed_trades():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    trades = [
        {"bot": "trend_bot", "symbol": "BTC", "pnl": 100, "closed_at": (start + timedelta(days=1)).isoformat()},
        {"bot": "trend_bot", "symbol": "ETH", "pnl": -40, "closed_at": (start + timedelta(days=2)).isoformat()},
        {"bot": "trend_bot", "symbol": "SOL", "pnl": 20, "closed_at": (start + timedelta(days=3)).isoformat()},
        {"bot": "trend_bot", "symbol": "HYPE", "pnl": -80, "closed_at": (start + timedelta(days=4)).isoformat()},
    ]
    report = summarize("Trend", 1000, trades)
    assert report["trade_count"] == 4
    assert report["wins"] == 2
    assert report["losses"] == 2
    assert report["win_rate_pct"] == 50.0
    assert report["total_pnl"] == 0
    assert report["avg_pnl"] == 0
    assert report["avg_win"] == 60
    assert report["avg_loss"] == -60
    assert report["biggest_loss"] == -80
    # 1000 -> 1100 -> 1060 -> 1080 -> 1000. Largest drop is 1100 to 1000.
    assert report["max_drawdown_usd"] == 100
    assert report["equity"] == 1000
    assert len(report["equity_curve"]) == 5
    assert report["worst_trades"][0]["symbol"] == "HYPE"

    empty = summarize("Trend", 1000, [])
    assert empty["trade_count"] == 0
    assert empty["win_rate_pct"] is None
    assert empty["equity_curve"][0]["equity"] == 1000


def test_dashboard_reads_real_allocation_and_closed_trades():
    from core.dashboard import DASHBOARD_HTML
    from core.orchestrator import Orchestrator
    from core.performance import PERFORMANCE_HTML
    from core.risk import RiskManager

    config = load_global_config("config/global.yaml")
    orchestrator = Orchestrator(config)
    orchestrator.risk = RiskManager(initial_equity=10_000)
    status = orchestrator.get_status()
    by_name = {row["key"]: row for row in status["allocation"]}
    assert by_name["meme_sniper"]["pct"] == 20.0
    assert by_name["meme_sniper"]["usd"] == 2000

    orchestrator.risk.closed_trades.append(
        _trade("trend_bot", "SOL/USDT:USDT", 25)
    )
    page = orchestrator.get_performance()
    assert page["portfolio"]["total_pnl"] == 25
    assert page["portfolio"]["trade_count"] == 1
    trend = next(bot for bot in page["bots"] if bot["key"] == "trend_bot")
    assert trend["total_pnl"] == 25
    assert trend["win_rate_pct"] == 100.0
    assert "Performance" in DASHBOARD_HTML
    assert "/performance" in DASHBOARD_HTML
    assert "portfolio_share" in PERFORMANCE_HTML
    assert "Win rate" in PERFORMANCE_HTML
    assert "/static/dagobert-bg.webp" in DASHBOARD_HTML
    assert "background-size: cover" in DASHBOARD_HTML
    assert "background-attachment: fixed" in DASHBOARD_HTML
    assert "background-position: center center" in DASHBOARD_HTML
    assert "/static/dagobert-bg.webp" in PERFORMANCE_HTML
    from core.dashboard import STATIC_BG
    assert STATIC_BG.is_file()
    assert STATIC_BG.stat().st_size < 300_000
    assert "money-duck" not in DASHBOARD_HTML
    assert "money-duck" not in PERFORMANCE_HTML


def _trade(bot, symbol, pnl):
    from core.risk import ClosedTrade

    return ClosedTrade(
        bot_name=bot,
        symbol=symbol,
        side="long",
        amount=1,
        entry_price=1,
        exit_price=1 + pnl,
        pnl=pnl,
        opened_at=datetime.now(timezone.utc),
    )


def test_public_candle_parser_knows_sol_and_hype():
    assert spot_symbol("SOL/USDT:USDT") == "SOLUSDT"
    assert spot_symbol("HYPE/USDT:USDT") == "HYPEUSDT"
    candles = parse_binance_klines([
        [1795122000000, "67.78", "67.86", "67.17", "67.36", "8601.2"],
    ])
    assert candles == [[1795122000000, 67.78, 67.86, 67.17, 67.36, 8601.2]]


def test_trend_bot_lists_sol_and_hype_and_sizes_for_four_coins():
    cfg = load_bot_config("config/trend_bot.yaml")
    assert cfg.symbols == [
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "SOL/USDT:USDT",
        "HYPE/USDT:USDT",
    ]
    assert cfg.risk.max_positions == 4
    assert cfg.risk.risk_per_trade_pct == 0.6


def test_trailing_stop_only_moves_forward():
    assert trail_stop("long", extreme=110, stop=95, distance=5) == 105
    assert trail_stop("long", extreme=100, stop=105, distance=5) == 105
    assert trail_stop("short", extreme=90, stop=110, distance=5) == 95
    assert trail_stop("short", extreme=100, stop=95, distance=5) == 95


def _frame(closes, volume=100.0, spread=0.4):
    rows = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, close in enumerate(closes):
        rows.append(
            {
                "timestamp": start + timedelta(minutes=15 * i),
                "open": close,
                "high": close + spread,
                "low": close - spread,
                "close": close,
                "volume": volume,
            }
        )
    return pd.DataFrame(rows)


def test_trend_buys_a_fresh_uptrend_and_ignores_chop():
    # Long enough for the averages, with the cross happening on the last bar.
    dip = [100 - i * 0.3 for i in range(70)]
    rise = dip + [dip[-1] + i * 2.2 for i in range(1, 3)]
    signal = TrendStrategy(
        {
            "fast_ema": 3,
            "slow_ema": 8,
            "adx_threshold": 10,
            "adx_period": 5,
            "atr_period": 5,
            "stop_loss_atr_mult": 1.5,
            "take_profit_atr_mult": 2.5,
        }
    ).generate_signal(_frame(rise, spread=0.5))
    assert signal.action == "buy"
    assert signal.meta["type"] == "ema_cross_long"
    assert signal.stop_loss < rise[-1] < signal.take_profit
    flat = TrendStrategy({"fast_ema": 8, "slow_ema": 20, "adx_threshold": 25}).generate_signal(
        _frame([100 + (0.01 if i % 2 == 0 else -0.01) for i in range(80)], spread=0.01)
    )
    assert flat.action == "hold"


def test_breakout_uses_the_prior_channel_not_its_own_high():
    closes = [10.0] * 80
    frame = _frame(closes, volume=100, spread=0.2)
    frame.loc[frame.index[-1], ["open", "high", "low", "close", "volume"]] = [10.2, 13.0, 10.1, 12.5, 500]
    signal = BreakoutStrategy(
        {
            "donchian_period": 20,
            "volume_mult": 1.5,
            "atr_expansion": 1.1,
            "min_atr_pct": 0.1,
            "ema_period": 10,
            "atr_period": 5,
            "atr_ma_period": 10,
        }
    ).generate_signal(frame)
    assert signal.action == "buy"
    assert signal.stop_loss < 12.5 < signal.take_profit


def test_mean_reversion_fades_a_quiet_stretch_and_skips_a_trend():
    quiet_prices = [100 + ((i % 5) - 2) * 0.15 for i in range(100)]
    quiet_prices[-1] = quiet_prices[-2] - 6
    quiet = MeanReversionStrategy(
        {"bb_period": 20, "bb_std": 2, "rsi_period": 14, "rsi_oversold": 35, "adx_max": 25, "atr_period": 14}
    ).generate_signal(_frame(quiet_prices, spread=0.2))
    assert quiet.action == "buy"
    assert quiet.take_profit > quiet_prices[-1]

    trend_prices = [100 + i * 0.8 for i in range(100)]
    trend_prices[-1] = trend_prices[-2] + 8
    blocked = MeanReversionStrategy(
        {"bb_period": 20, "bb_std": 2, "rsi_period": 14, "rsi_overbought": 65, "adx_max": 25, "atr_period": 14}
    ).generate_signal(_frame(trend_prices, spread=0.3))
    assert blocked.action == "hold"
