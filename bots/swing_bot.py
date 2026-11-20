"""Paper-only swing bot. It buys dips inside an uptrend and never sends a live order."""

from __future__ import annotations

import asyncio
import logging

from backtest.data import ensure_cache, frame_from_rows
from backtest.swing import SwingParams, simulate_swing_portfolio
from bots.ensemble_trend_bot import _btc_above_average
from core.portfolio_guard import PortfolioGuard

logger = logging.getLogger(__name__)

FORWARD_TEST_LABEL = "Paper forward test – did not pass all backtest criteria"


class SwingBot:
    def __init__(self, config, allocated_equity: float, trading_mode: str = "paper") -> None:
        self.config = config
        self.name = config.name
        self.allocated_equity = float(allocated_equity)
        self.equity = float(allocated_equity)
        self.trading_mode = "paper"
        if str(trading_mode).lower() != "paper":
            logger.warning("Swing bot refused live mode (%s). Staying on paper.", trading_mode)
        self.running = False
        self.guard: PortfolioGuard | None = None
        self.weights: dict[str, float] = {}
        self.last_prices: dict[str, float] = {}
        self.paper_orders: list[dict] = []
        self.last_decision_date: str | None = None
        self.status_note = FORWARD_TEST_LABEL
        self._task: asyncio.Task | None = None

    @classmethod
    def from_config(cls, config, exchange, risk_manager, metrics, telegram, allocated_equity, trading_mode="paper"):
        del exchange, risk_manager, metrics, telegram
        return cls(config, allocated_equity, trading_mode)

    def attach_guard(self, guard: PortfolioGuard) -> None:
        self.guard = guard
        guard.update_bot(self.name, self.equity)
        guard.update_portfolio(self.equity)

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self.running:
            try:
                await asyncio.to_thread(self._rebalance)
            except Exception as exc:
                self.status_note = f"Daily check failed: {exc}"
                logger.warning("swing_rebalance_failed %s", exc)
            try:
                await asyncio.sleep(60 * 60)
            except asyncio.CancelledError:
                raise

    def run_daily_decision(self, coins: dict, decision_date: str) -> list[dict]:
        """Place paper buys and sells for one daily bar. The same date does nothing twice."""
        if not decision_date or decision_date == self.last_decision_date:
            return []
        params = self._params()
        regime = _btc_above_average(coins["BTCUSDT"]) if "BTCUSDT" in coins else None
        result = simulate_swing_portfolio(coins, params, initial_equity=max(self.equity, 1.0), regime=regime)
        scale = self.guard.exposure_scale(self.name) if self.guard else 1.0
        targets = {symbol: weight * scale for symbol, weight in result.ending_weights.items()}
        orders = []
        for symbol, new_weight in targets.items():
            old_weight = self.weights.get(symbol, 0.0)
            frame = coins.get(symbol) or {}
            closes = frame.get("close") or []
            price = closes[-1] if closes else self.last_prices.get(symbol)
            if not price or price <= 0:
                continue
            if abs(new_weight - old_weight) < 1e-4:
                self.last_prices[symbol] = price
                continue
            side = "buy" if new_weight > old_weight else "sell"
            usd = abs(new_weight - old_weight) * self.equity
            fill = price * (1.0 + params.cost if side == "buy" else 1.0 - params.cost)
            orders.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "usd": round(usd, 2),
                    "price": fill,
                    "signal_price": price,
                    "mode": "paper",
                    "date": decision_date,
                }
            )
            self.last_prices[symbol] = price
        self.weights = targets
        self.last_decision_date = decision_date
        self.paper_orders.extend(orders)
        if self.guard is not None:
            self.guard.update_bot(self.name, self.equity)
            self.guard.update_portfolio(self.equity)
        blocked = regime is not None and not regime.get(decision_date, False)
        if blocked and not any(weight > 0 for weight in targets.values()):
            self.status_note = FORWARD_TEST_LABEL + ". Bitcoin is under its 200-day average, so this bot stays in cash."
        elif orders:
            self.status_note = f"{FORWARD_TEST_LABEL}. Last daily paper fills: {len(orders)}."
        else:
            self.status_note = FORWARD_TEST_LABEL + ". No weight change today."
        logger.info("swing_paper_orders %s", orders)
        return orders

    def _rebalance(self) -> None:
        symbols = {
            "BTCUSDT": "2017-08-17",
            "ETHUSDT": "2017-08-17",
            "SOLUSDT": "2020-08-11",
            "HYPEUSDT": "2024-01-01",
        }
        data = ensure_cache(symbols)
        coins = {symbol: frame_from_rows(rows) for symbol, rows in data.items() if rows}
        latest = max(frame["date"][-1] for frame in coins.values() if frame.get("date"))
        self.run_daily_decision(coins, latest)

    def _params(self) -> SwingParams:
        raw = self.config.parameters
        return SwingParams(
            sma_trend=int(raw.get("sma_trend", 100)),
            rsi_length=int(raw.get("rsi_length", 2)),
            rsi_entry=float(raw.get("rsi_entry", 10)),
            rsi_exit=float(raw.get("rsi_exit", 70)),
            atr_length=int(raw.get("atr_length", 14)),
            atr_mult=float(raw.get("atr_mult", 2)),
            max_hold=int(raw.get("max_hold", 12)),
            target_vol=float(raw.get("target_vol", 0.15)),
            vol_lookback=int(raw.get("vol_lookback", 90)),
            fee=float(raw.get("fee_rate", 0.001)),
            slippage=float(raw.get("slippage_rate", 0.001)),
            per_coin_cap=float(raw.get("per_coin_cap", 0.25)),
            gross_cap=float(raw.get("gross_cap", 0.60)),
        )
