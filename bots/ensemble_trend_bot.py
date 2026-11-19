"""Paper-only daily Donchian ensemble. It never sends a live order."""

from __future__ import annotations

import asyncio
import logging

from backtest.data import ensure_cache, frame_from_rows
from backtest.ensemble import EnsembleParams, simulate_portfolio
from core.portfolio_guard import PortfolioGuard

logger = logging.getLogger(__name__)


class EnsembleTrendBot:
    def __init__(self, config, allocated_equity: float, trading_mode: str = "paper") -> None:
        self.config = config
        self.name = config.name
        self.allocated_equity = float(allocated_equity)
        self.equity = float(allocated_equity)
        self.trading_mode = "paper"
        if str(trading_mode).lower() != "paper":
            logger.warning("Ensemble trend refused live mode (%s). Staying on paper.", trading_mode)
        self.running = False
        self.guard: PortfolioGuard | None = None
        self.weights: dict[str, float] = {}
        self.last_prices: dict[str, float] = {}
        self.status_note = "Waiting for the first daily check."
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
                logger.warning("ensemble_rebalance_failed %s", exc)
            try:
                await asyncio.sleep(6 * 60 * 60)
            except asyncio.CancelledError:
                raise

    def _rebalance(self) -> None:
        symbols = {
            "BTCUSDT": "2017-08-17",
            "ETHUSDT": "2017-08-17",
            "SOLUSDT": "2020-08-11",
            "HYPEUSDT": "2024-01-01",
        }
        data = ensure_cache(symbols)
        coins = {symbol: frame_from_rows(rows) for symbol, rows in data.items() if rows}
        params = self._params()
        regime = None
        if self.config.parameters.get("regime_filter"):
            from backtest.harness import _regime_map
            regime = _regime_map(data["BTCUSDT"])
        result = simulate_portfolio(coins, params, initial_equity=self.allocated_equity, regime=regime)
        scale = 1.0
        if self.guard is not None:
            self.guard.update_bot(self.name, self.equity)
            self.guard.update_portfolio(self.equity)
            scale = self.guard.exposure_scale(self.name)
        self.weights = {symbol: weight * scale for symbol, weight in result.ending_weights.items()}
        for symbol, frame in coins.items():
            if frame["close"]:
                self.last_prices[symbol] = frame["close"][-1]
        self.status_note = (
            f"Paper weights updated. Risk state {self.guard.state if self.guard else 'normal'}."
        )
        logger.info("ensemble_weights %s", {key: round(value, 3) for key, value in self.weights.items()})

    def _params(self) -> EnsembleParams:
        raw = self.config.parameters
        return EnsembleParams(
            target_vol=float(raw.get("target_vol", 0.25)),
            vol_lookback=int(raw.get("vol_lookback", 90)),
            rebalance_band=float(raw.get("rebalance_band", 0.20)),
            fee=float(raw.get("fee_rate", 0.001)),
            slippage=float(raw.get("slippage_rate", 0.001)),
            per_coin_cap=float(raw.get("per_coin_cap", 0.40)),
            gross_cap=float(raw.get("gross_cap", 1.0)),
        )
