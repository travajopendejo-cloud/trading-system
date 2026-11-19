"""Background loop: discover tokens, watch wallets, and mark paper exits.

Network errors are logged and skipped. They do not stop the rest of the app.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from meme_sniper.core.discovery import TokenDiscovery
from meme_sniper.core.filters import TokenInfo
from meme_sniper.core.http_client import HttpJson
from meme_sniper.core.parsers import ObservedBuy
from meme_sniper.core.safety import SafetyChecker
from meme_sniper.core.settings import describe_rpc, helius_api_key, solana_rpc_url
from meme_sniper.core.sniper import MemeSniper, assert_paper_only
from meme_sniper.core.wallet_copy import TRACKED_WALLETS
from meme_sniper.core.wallet_monitor import WalletMonitor

logger = logging.getLogger(__name__)


class MemeSniperService:
    def __init__(
        self,
        config: dict[str, Any],
        http: HttpJson | None = None,
        discovery: TokenDiscovery | None = None,
        wallets: WalletMonitor | None = None,
        safety: SafetyChecker | None = None,
    ) -> None:
        self.config = config
        self.sniper = MemeSniper(config)
        sources = config.get("sources") or {}
        self.discovery_enabled = bool(sources.get("discovery", True))
        self.copy_enabled = bool(sources.get("wallet_copy", True))
        self.interval = max(10.0, float(sources.get("poll_interval_seconds", 45)))
        self.min_wallet_buy_usd = float(sources.get("min_wallet_buy_usd", 25))
        self.http = http or HttpJson()
        rpc_url = solana_rpc_url()
        self.rpc_description = describe_rpc(rpc_url)
        self.safety = safety or SafetyChecker(self.http, rpc_url)
        min_liquidity = float((config.get("filters") or {}).get("min_liquidity_usd", 800))
        self.discovery = discovery or TokenDiscovery(
            self.http,
            max_age_seconds=float(sources.get("max_token_age_minutes", 180)) * 60.0,
            max_per_poll=int(sources.get("max_tokens_per_poll", 3)),
        )
        if discovery is None:
            self.discovery.min_liquidity_usd = min_liquidity
        self.wallets = wallets or WalletMonitor(
            wallets=list(TRACKED_WALLETS),
            http=self.http,
            rpc_url=rpc_url,
            helius_api_key=helius_api_key(),
            max_signatures=int(sources.get("max_signatures_per_poll", 25)),
            max_transactions=int(sources.get("max_transactions_per_poll", 8)),
        )
        self.running = False
        self.last_error: str | None = None
        self.cycles = 0
        self._attempts: dict[str, int] = {}

    async def run(self) -> None:
        try:
            assert_paper_only(self.sniper)
        except Exception as exc:
            self.last_error = str(exc)
            self.running = False
            logger.error("%s", exc)
            return

        self.running = True
        helius_state = "on" if self.wallets.helius_api_key else "off, using public RPC if it answers"
        logger.info("Meme sniper paper loop starting. RPC: %s. Helius: %s", self.rpc_description, helius_state)
        for wallet in TRACKED_WALLETS:
            state = "ON" if wallet.enabled else "OFF"
            logger.info("Tracked wallet %s %s — %s", state, wallet.name, wallet.notes)

        if self.copy_enabled:
            await self.wallets.prime()

        while self.running:
            try:
                await self.cycle()
            except asyncio.CancelledError:
                self.running = False
                raise
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Meme sniper cycle failed")
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                self.running = False
                raise

    async def cycle(self) -> None:
        self.cycles += 1
        if self.discovery_enabled:
            try:
                tokens = await self.discovery.poll()
            except Exception as exc:
                self.last_error = str(exc)
                logger.warning("Discovery failed: %s", exc)
                tokens = []
            for token in tokens:
                await self._consider_new_token(token)

        if self.copy_enabled:
            try:
                buys = await self.wallets.poll()
            except Exception as exc:
                self.last_error = str(exc)
                logger.warning("Wallet poll failed: %s", exc)
                buys = []
            for buy in buys:
                await self._consider_copy(buy)

        await self._refresh_exits()

    async def _consider_new_token(self, token: TokenInfo) -> None:
        if token.price <= 0 or token.liquidity_usd < self.sniper.filters.min_liquidity_usd:
            self.discovery.seen.add(token.address)
            logger.info(
                "Skipped %s before safety checks: liquidity $%.0f",
                token.symbol,
                token.liquidity_usd,
            )
            return
        enriched = await self._safe_enrich(token)
        if not enriched.safety_checked:
            attempts = self._attempts.get(token.address, 0) + 1
            self._attempts[token.address] = attempts
            if attempts >= 3:
                self.discovery.seen.add(token.address)
            logger.info(
                "Safety data not ready for %s (%s)",
                token.symbol,
                enriched.safety_note or "unknown",
            )
            return
        self.discovery.seen.add(token.address)
        self.sniper.open_trade(enriched, source=enriched.source or "discovery")

    async def _consider_copy(self, buy: ObservedBuy) -> None:
        try:
            token = await self.discovery.lookup(buy.token_address, max_age_seconds=None)
        except Exception as exc:
            logger.info("Copy lookup delayed for %s: %s", buy.token_address[:6], exc)
            return
        if token is None:
            logger.info("Copy skipped, no market data for %s", buy.token_address[:6])
            return
        token.source = "copy"
        enriched = await self._safe_enrich(token)
        allowed, reason = self.sniper.evaluate_token(enriched)
        if not allowed:
            logger.info("Copy skipped for %s: %s", enriched.symbol, reason)
            return
        spent = enriched.price * buy.token_amount if enriched.price > 0 else 0.0
        signal = self.sniper.wallet_copy.should_copy(
            wallet_address=buy.wallet_address,
            token_address=buy.token_address,
            token_symbol=enriched.symbol,
            entry_price=enriched.price,
            size_usd=spent,
            min_wallet_buy_usd=self.min_wallet_buy_usd,
        )
        if signal is None:
            logger.info("Copy not taken for %s (wallet spent about $%.0f)", enriched.symbol, spent)
            return
        self.sniper.handle_copy_signal(signal, enriched)

    async def _safe_enrich(self, token: TokenInfo) -> TokenInfo:
        try:
            return await self.safety.enrich(token)
        except Exception as exc:
            token.safety_checked = False
            token.safety_note = str(exc)
            self.last_error = str(exc)
            return token

    async def _refresh_exits(self) -> None:
        now = datetime.now(timezone.utc)
        for key, pos in list(self.sniper.risk.positions.items()):
            price = None
            if pos.token_address:
                try:
                    price = await self.discovery.price_usd(pos.token_address)
                except Exception as exc:
                    logger.info("Price refresh failed for %s: %s", pos.symbol, exc)
            if price is None or price <= 0:
                continue
            self.sniper.check_position(key, price, now=now)

    def dashboard_positions(self) -> list[dict]:
        return self.sniper.dashboard_positions()

    def dashboard_trades(self, limit: int = 15) -> list[dict]:
        return self.sniper.dashboard_trades(limit)

    def status_summary(self) -> dict[str, Any]:
        return {
            "mode": "paper",
            "enabled": self.running,
            "capital": round(self.sniper.risk.capital, 2),
            "daily_pnl": round(self.sniper.risk.daily_pnl, 2),
            "tracked_wallets": [wallet.name for wallet in TRACKED_WALLETS if wallet.enabled],
            "disabled_wallets": [wallet.name for wallet in TRACKED_WALLETS if not wallet.enabled],
            "last_error": self.last_error,
            "rpc": self.rpc_description,
            "helius": bool(self.wallets.helius_api_key),
        }
