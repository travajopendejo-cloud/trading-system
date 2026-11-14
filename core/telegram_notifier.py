"""Non-blocking Telegram notification service."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        enabled: bool = True,
        rate_limit_per_minute: int = 20,
    ) -> None:
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled and bool(token) and bool(chat_id)
        self.rate_limit = rate_limit_per_minute
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self.enabled:
            self._client = httpx.AsyncClient(timeout=10.0)
            logger.info("telegram_notifier_started")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("telegram_notifier_stopped")

    async def _under_rate_limit(self) -> bool:
        async with self._lock:
            now = datetime.now(timezone.utc).timestamp()
            while self._timestamps and now - self._timestamps[0] > 60:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.rate_limit:
                return False
            self._timestamps.append(now)
            return True

    async def send(self, message: str, parse_mode: str = "HTML") -> None:
        if not self.enabled or not self._client:
            return
        if not await self._under_rate_limit():
            logger.warning("telegram_rate_limited")
            return

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            resp = await self._client.post(url, json=payload)
            if resp.status_code != 200:
                logger.error("telegram_send_failed", status=resp.status_code, body=resp.text)
        except Exception as exc:
            logger.exception("telegram_send_error", error=str(exc))

    async def notify_trade(
        self,
        bot_name: str,
        side: str,
        symbol: str,
        amount: float,
        price: float,
        action: str = "ENTRY",
        pnl: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        emoji = "🟢" if side.lower() == "buy" else "🔴"
        msg = (
            f"{emoji} <b>{action}</b> | <b>{bot_name}</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Side: {side.upper()} | Size: {amount:.6f}\n"
            f"Price: {price:.4f}"
        )
        if pnl is not None:
            pnl_emoji = "✅" if pnl >= 0 else "❌"
            msg += f"\n{pnl_emoji} PnL: {pnl:.4f} USDT"
        if extra:
            for k, v in extra.items():
                msg += f"\n{k}: {v}"
        await self.send(msg)

    async def notify_error(self, bot_name: str, error: str) -> None:
        msg = f"🚨 <b>ERROR</b> | <b>{bot_name}</b>\n<code>{error[:800]}</code>"
        await self.send(msg)

    async def notify_status(self, title: str, body: str) -> None:
        msg = f"ℹ️ <b>{title}</b>\n{body}"
        await self.send(msg)
