"""Exchange client wrapper around ccxt with reconnect handling."""

from __future__ import annotations

import asyncio
from typing import Any
import logging

logger = logging.getLogger(__name__)

try:
    import ccxt.async_support as ccxt
    CCXT_AVAILABLE = True
except ImportError:
    ccxt = None  # type: ignore
    CCXT_AVAILABLE = False
    logger.warning("ccxt not installed – use TRADING_MODE=mock")


class ExchangeClient:
    def __init__(
        self,
        exchange_id: str,
        api_key: str = "",
        api_secret: str = "",
        password: str = "",
        sandbox: bool = True,
        default_type: str = "future",
        options: dict[str, Any] | None = None,
    ) -> None:
        if not CCXT_AVAILABLE:
            raise RuntimeError(
                "ccxt is not installed. Install it with 'pip install ccxt' "
                "or run with TRADING_MODE=mock"
            )
        self.exchange_id = exchange_id
        self.sandbox = sandbox
        self._config = {
            "apiKey": api_key,
            "secret": api_secret,
            "password": password,
            "enableRateLimit": True,
            "options": options or {"defaultType": default_type},
        }
        self.exchange = None

    async def connect(self) -> None:
        exchange_class = getattr(ccxt, self.exchange_id)
        self.exchange = exchange_class(self._config)
        if self.sandbox and hasattr(self.exchange, "set_sandbox_mode"):
            self.exchange.set_sandbox_mode(True)
        await self.exchange.load_markets()
        logger.info(
            "exchange_connected exchange=%s sandbox=%s markets=%s",
            self.exchange_id, self.sandbox, len(self.exchange.markets),
        )

    async def close(self) -> None:
        if self.exchange:
            await self.exchange.close()
            self.exchange = None
            logger.info("exchange_closed")

    def _ensure_connected(self):
        if not self.exchange:
            raise RuntimeError("Exchange not connected")
        return self.exchange

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 200, since: int | None = None) -> list[list]:
        ex = self._ensure_connected()
        return await ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, since=since)

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        ex = self._ensure_connected()
        return await ex.fetch_ticker(symbol)

    async def create_order(
        self, symbol: str, side: str, amount: float,
        order_type: str = "market", price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ex = self._ensure_connected()
        params = params or {}
        logger.info("creating_order symbol=%s side=%s amount=%s", symbol, side, amount)
        return await ex.create_order(symbol=symbol, type=order_type, side=side, amount=amount, price=price, params=params)

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        return await self._ensure_connected().cancel_order(order_id, symbol)

    async def fetch_balance(self) -> dict[str, Any]:
        return await self._ensure_connected().fetch_balance()

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[dict]:
        ex = self._ensure_connected()
        if hasattr(ex, "fetch_positions"):
            return await ex.fetch_positions(symbols)
        return []

    async def set_leverage(self, leverage: int, symbol: str) -> None:
        ex = self._ensure_connected()
        if hasattr(ex, "set_leverage"):
            await ex.set_leverage(leverage, symbol)
