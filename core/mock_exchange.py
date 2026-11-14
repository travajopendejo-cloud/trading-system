"""Mock exchange that generates synthetic OHLCV data for demo / testing."""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class MockExchangeClient:
    """Drop-in replacement for ExchangeClient that needs no network."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        random.seed(seed)
        np.random.seed(seed)
        self._prices: dict[str, float] = {
            "BTC/USDT:USDT": 65_000.0,
            "ETH/USDT:USDT": 3_200.0,
            "SOL/USDT:USDT": 150.0,
            "BNB/USDT:USDT": 580.0,
        }
        self._connected = False
        self.markets = {s: {} for s in self._prices}

    async def connect(self) -> None:
        self._connected = True
        logger.info("mock_exchange_connected", symbols=list(self._prices.keys()))

    async def close(self) -> None:
        self._connected = False
        logger.info("mock_exchange_closed")

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("Mock exchange not connected")

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 200,
        since: int | None = None,
    ) -> list[list]:
        self._ensure_connected()
        await asyncio.sleep(0.05)  # simulate network latency

        tf_minutes = {
            "1m": 1, "3m": 3, "5m": 5, "15m": 15,
            "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440,
        }.get(timeframe, 15)

        base_price = self._prices.get(symbol, 100.0)
        now = datetime.now(timezone.utc)
        candles = []

        # Random walk with mild trend + volatility clustering
        price = base_price * (0.92 + random.random() * 0.08)
        for i in range(limit - 1, -1, -1):
            ts = now - timedelta(minutes=tf_minutes * i)
            # Geometric Brownian-ish step
            ret = np.random.normal(0.00015, 0.004)
            price = max(price * (1 + ret), 1.0)
            high = price * (1 + abs(np.random.normal(0, 0.002)))
            low = price * (1 - abs(np.random.normal(0, 0.002)))
            open_p = price * (1 + np.random.normal(0, 0.001))
            vol = abs(np.random.normal(100, 40)) * (1 + abs(ret) * 50)
            candles.append([
                int(ts.timestamp() * 1000),
                round(open_p, 4),
                round(high, 4),
                round(low, 4),
                round(price, 4),
                round(vol, 2),
            ])

        # Update last price so successive calls drift
        self._prices[symbol] = price
        return candles

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        self._ensure_connected()
        price = self._prices.get(symbol, 100.0)
        return {
            "symbol": symbol,
            "last": price,
            "bid": price * 0.9999,
            "ask": price * 1.0001,
            "timestamp": int(time.time() * 1000),
        }

    async def create_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_connected()
        await asyncio.sleep(0.02)
        fill_price = self._prices.get(symbol, 100.0)
        if price:
            fill_price = price
        # Small random slippage
        slip = fill_price * random.uniform(-0.0003, 0.0003)
        fill_price += slip if side == "buy" else -slip

        order_id = f"mock-{symbol}-{int(time.time()*1000)}"
        logger.info(
            "mock_order_filled",
            symbol=symbol,
            side=side,
            amount=amount,
            price=round(fill_price, 4),
            order_id=order_id,
        )
        return {
            "id": order_id,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "amount": amount,
            "price": fill_price,
            "average": fill_price,
            "status": "closed",
            "timestamp": int(time.time() * 1000),
        }

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        return {"id": order_id, "status": "canceled"}

    async def fetch_balance(self) -> dict[str, Any]:
        return {
            "USDT": {"free": 10_000.0, "used": 0.0, "total": 10_000.0},
            "free": {"USDT": 10_000.0},
            "total": {"USDT": 10_000.0},
        }

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[dict]:
        return []

    async def set_leverage(self, leverage: int, symbol: str) -> None:
        pass
