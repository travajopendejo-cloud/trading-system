"""Base trading bot implementation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import structlog

from core.config import BotConfig
from core.exchange import ExchangeClient
from core.risk import RiskManager, Position
from core.metrics import Metrics
from core.telegram_notifier import TelegramNotifier
from strategies.base import BaseStrategy, Signal

logger = structlog.get_logger(__name__)


class BaseBot:
    def __init__(
        self,
        config: BotConfig,
        strategy: BaseStrategy,
        exchange: ExchangeClient,
        risk_manager: RiskManager,
        metrics: Metrics,
        telegram: TelegramNotifier,
        allocated_equity: float,
        trading_mode: str = "paper",
    ) -> None:
        self.config = config
        self.name = config.name
        self.strategy = strategy
        self.exchange = exchange
        self.risk = risk_manager
        self.metrics = metrics
        self.telegram = telegram
        self.allocated_equity = allocated_equity
        self.trading_mode = trading_mode

        self.running = False
        self._task: asyncio.Task | None = None
        self._ohlcv_cache: dict[str, pd.DataFrame] = {}
        self.open_positions: dict[str, dict[str, Any]] = {}  # symbol -> info

        self.log = logger.bind(bot=self.name)

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.metrics.set_bot_status(self.name, 1)
        self._task = asyncio.create_task(self._run_loop())
        self.log.info("bot_started", symbols=self.config.symbols, mode=self.trading_mode)

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.metrics.set_bot_status(self.name, 0)
        self.log.info("bot_stopped")

    async def _run_loop(self) -> None:
        interval = self._timeframe_to_seconds(self.config.timeframe)
        while self.running:
            try:
                await self._process_symbols()
            except Exception as exc:
                self.log.exception("bot_loop_error", error=str(exc))
                self.metrics.record_error(self.name, "loop")
                await self.telegram.notify_error(self.name, str(exc))
                self.metrics.set_bot_status(self.name, -1)
            await asyncio.sleep(min(interval, 30))  # poll at most every 30s for safety

    async def _process_symbols(self) -> None:
        for symbol in self.config.symbols:
            try:
                df = await self._fetch_ohlcv(symbol)
                if df is None or len(df) < 50:
                    continue
                signal = self.strategy.generate_signal(df)
                await self._handle_signal(symbol, signal, df)
            except Exception as exc:
                self.log.exception("symbol_process_error", symbol=symbol, error=str(exc))
                self.metrics.record_error(self.name, "symbol")

    async def _fetch_ohlcv(self, symbol: str) -> pd.DataFrame | None:
        raw = await self.exchange.fetch_ohlcv(
            symbol,
            timeframe=self.config.timeframe,
            limit=self.config.lookback_candles,
        )
        if not raw:
            return None
        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        self._ohlcv_cache[symbol] = df
        return df

    async def _handle_signal(
        self, symbol: str, signal: Signal, df: pd.DataFrame
    ) -> None:
        current_pos = self.open_positions.get(symbol)

        # Manage existing position (SL / TP)
        if current_pos:
            await self._check_exit(symbol, current_pos, df.iloc[-1])
            return

        if signal.action == "hold":
            return

        # New entry
        if signal.action in ("buy", "sell"):
            await self._open_position(symbol, signal, df.iloc[-1])

    async def _open_position(
        self, symbol: str, signal: Signal, candle: pd.Series
    ) -> None:
        price = float(candle["close"])
        stop = signal.stop_loss or (
            price * 0.98 if signal.action == "buy" else price * 1.02
        )

        size = self.risk.calculate_position_size(
            equity=self.allocated_equity,
            risk_pct=self.config.risk.risk_per_trade_pct,
            entry_price=price,
            stop_price=stop,
        )
        if size <= 0:
            return

        notional = size * price
        can_open, reason = self.risk.can_open_position(self.name, notional)
        if not can_open:
            self.log.info("position_rejected", symbol=symbol, reason=reason)
            return

        # Check max positions for this bot
        if len(self.open_positions) >= self.config.risk.max_positions:
            self.log.info("max_bot_positions_reached", symbol=symbol)
            return

        side = "buy" if signal.action == "buy" else "sell"

        if self.trading_mode == "live":
            start = datetime.now(timezone.utc)
            order = await self.exchange.create_order(
                symbol=symbol,
                side=side,
                amount=size,
                order_type=self.config.execution.get("order_type", "market"),
            )
            latency = (datetime.now(timezone.utc) - start).total_seconds()
            self.metrics.record_order_latency(self.name, latency)
            fill_price = float(order.get("average") or order.get("price") or price)
        else:
            # Paper fill
            fill_price = price
            order = {"id": f"paper-{symbol}-{datetime.now().timestamp()}", "status": "closed"}

        pos = Position(
            symbol=symbol,
            side="long" if side == "buy" else "short",
            amount=size,
            entry_price=fill_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            bot_name=self.name,
        )
        self.risk.add_position(pos)
        self.open_positions[symbol] = {
            "side": pos.side,
            "amount": size,
            "entry_price": fill_price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "order_id": order.get("id"),
        }

        self.log.info(
            "position_opened",
            symbol=symbol,
            side=side,
            size=size,
            price=fill_price,
            mode=self.trading_mode,
        )
        await self.telegram.notify_trade(
            bot_name=self.name,
            side=side,
            symbol=symbol,
            amount=size,
            price=fill_price,
            action="ENTRY",
        )
        self.metrics.set_open_positions(len(self.risk.positions), scope="total")
        self.metrics.set_open_positions(len(self.open_positions), scope=self.name)

    async def _check_exit(
        self, symbol: str, pos: dict[str, Any], candle: pd.Series
    ) -> None:
        price = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        side = pos["side"]
        entry = pos["entry_price"]
        sl = pos.get("stop_loss")
        tp = pos.get("take_profit")

        hit_sl = False
        hit_tp = False

        if side == "long":
            if sl and low <= sl:
                hit_sl = True
                exit_price = sl
            elif tp and high >= tp:
                hit_tp = True
                exit_price = tp
            else:
                return
        else:  # short
            if sl and high >= sl:
                hit_sl = True
                exit_price = sl
            elif tp and low <= tp:
                hit_tp = True
                exit_price = tp
            else:
                return

        await self._close_position(
            symbol, pos, exit_price, reason="stop_loss" if hit_sl else "take_profit"
        )

    async def _close_position(
        self,
        symbol: str,
        pos: dict[str, Any],
        exit_price: float,
        reason: str,
    ) -> None:
        side = "sell" if pos["side"] == "long" else "buy"
        amount = pos["amount"]

        if self.trading_mode == "live":
            await self.exchange.create_order(
                symbol=symbol,
                side=side,
                amount=amount,
                order_type="market",
                params={"reduceOnly": True},
            )

        entry = pos["entry_price"]
        if pos["side"] == "long":
            pnl = (exit_price - entry) * amount
        else:
            pnl = (entry - exit_price) * amount

        # Record the closed trade in history
        self.risk.close_position(
            bot_name=self.name,
            symbol=symbol,
            exit_price=exit_price,
            pnl=pnl,
        )

        del self.open_positions[symbol]

        win = pnl > 0
        self.metrics.record_trade(self.name, win)
        self.metrics.set_open_positions(len(self.risk.positions), scope="total")
        self.metrics.set_open_positions(len(self.open_positions), scope=self.name)

        self.log.info(
            "position_closed",
            symbol=symbol,
            reason=reason,
            pnl=round(pnl, 4),
            exit_price=exit_price,
        )
        await self.telegram.notify_trade(
            bot_name=self.name,
            side=side,
            symbol=symbol,
            amount=amount,
            price=exit_price,
            action=reason.upper(),
            pnl=pnl,
        )

    @staticmethod
    def _timeframe_to_seconds(tf: str) -> int:
        mapping = {
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "2h": 7200,
            "4h": 14400,
            "1d": 86400,
        }
        return mapping.get(tf, 900)
