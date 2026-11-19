"""Find new Solana tokens from pump.fun (when reachable) and DexScreener."""

from __future__ import annotations

import logging
import time

from meme_sniper.core.constants import (
    DEXSCREENER_BOOSTS_URL,
    DEXSCREENER_PROFILES_URL,
    DEXSCREENER_TOKEN_URL,
    PUMP_LATEST_URL,
)
from meme_sniper.core.filters import TokenInfo
from meme_sniper.core.http_client import HttpJson
from meme_sniper.core.parsers import (
    parse_dexscreener_profiles,
    parse_pump_coins,
    token_info_from_dexscreener,
)

logger = logging.getLogger(__name__)


class TokenDiscovery:
    def __init__(
        self,
        http: HttpJson,
        max_age_seconds: float = 180 * 60,
        max_per_poll: int = 3,
    ) -> None:
        self.http = http
        self.max_age_seconds = max_age_seconds
        self.max_per_poll = max(1, int(max_per_poll))
        self.min_liquidity_usd = 0.0
        self.seen: set[str] = set()
        self._price_cache: dict[str, tuple[float, float]] = {}

    async def poll(self) -> list[TokenInfo]:
        """Return up to max_per_poll new tokens that are young enough to consider.

        Tokens that fail a temporary lookup are left unseen so a later poll can retry.
        Tokens that are simply too old are marked seen.
        """
        found: list[TokenInfo] = []
        lookups = 0
        lookup_budget = self.max_per_poll * 4
        for mint in await self._candidate_mints():
            if mint in self.seen:
                continue
            if len(found) >= self.max_per_poll or lookups >= lookup_budget:
                break
            lookups += 1
            try:
                token = await self.lookup(mint, max_age_seconds=self.max_age_seconds)
            except Exception as exc:
                logger.info("Token lookup delayed for %s: %s", mint[:6], exc)
                continue
            if token is None or token.price <= 0 or token.liquidity_usd < self.min_liquidity_usd:
                self.seen.add(mint)
                continue
            found.append(token)
        self._trim_seen()
        return found

    async def lookup(self, mint: str, max_age_seconds: float | None = None) -> TokenInfo | None:
        payload = await self.http.get_json(DEXSCREENER_TOKEN_URL.format(mint=mint))
        return token_info_from_dexscreener(payload, mint, max_age_seconds=max_age_seconds)

    async def price_usd(self, mint: str) -> float | None:
        now = time.monotonic()
        cached = self._price_cache.get(mint)
        if cached and now - cached[0] < 20:
            return cached[1]
        try:
            token = await self.lookup(mint, max_age_seconds=None)
        except Exception:
            return cached[1] if cached else None
        if token is None or token.price <= 0:
            return None
        self._price_cache[mint] = (now, token.price)
        return token.price

    async def _candidate_mints(self) -> list[str]:
        sources = (
            ("pump.fun", PUMP_LATEST_URL, parse_pump_coins),
            ("dexscreener profiles", DEXSCREENER_PROFILES_URL, parse_dexscreener_profiles),
            ("dexscreener boosts", DEXSCREENER_BOOSTS_URL, parse_dexscreener_profiles),
        )
        found: list[str] = []
        for label, url, parser in sources:
            try:
                payload = await self.http.get_json(url)
            except Exception as exc:
                logger.info("%s discovery unavailable: %s", label, exc)
                continue
            try:
                found.extend(parser(payload))
            except Exception as exc:
                logger.info("%s discovery payload ignored: %s", label, exc)
        unique: list[str] = []
        known: set[str] = set()
        for mint in found:
            if mint in known:
                continue
            known.add(mint)
            unique.append(mint)
        return unique

    def _trim_seen(self) -> None:
        if len(self.seen) > 5000:
            self.seen = set(list(self.seen)[-3000:])
