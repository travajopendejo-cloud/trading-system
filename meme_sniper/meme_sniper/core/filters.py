"""Token filters for the Meme Sniper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TokenInfo:
    symbol: str
    address: str
    price: float
    liquidity_usd: float
    dev_buy_pct: float = 0.0
    bundle_pct: float = 0.0
    top10_holders_pct: float = 0.0
    sniper_wallets: int = 0
    age_seconds: float = 0.0


class TokenFilter:
    def __init__(self, config: dict[str, Any]):
        filters = config.get("filters", {})
        self.max_dev_buy_pct = float(filters.get("max_dev_buy_pct", 8.0))
        self.max_bundle_pct = float(filters.get("max_bundle_pct", 12.0))
        self.max_top10_holders_pct = float(filters.get("max_top10_holders_pct", 35.0))
        self.min_liquidity_usd = float(filters.get("min_liquidity_usd", 800))
        self.max_sniper_wallets = int(filters.get("max_sniper_wallets", 15))

    def is_valid(self, token: TokenInfo) -> tuple[bool, str]:
        """
        Returns (is_valid, reason)
        """
        if token.liquidity_usd < self.min_liquidity_usd:
            return False, f"Liquidity too low ({token.liquidity_usd:.0f})"

        if token.dev_buy_pct > self.max_dev_buy_pct:
            return False, f"Dev buy too high ({token.dev_buy_pct:.1f}%)"

        if token.bundle_pct > self.max_bundle_pct:
            return False, f"Bundle concentration too high ({token.bundle_pct:.1f}%)"

        if token.top10_holders_pct > self.max_top10_holders_pct:
            return False, f"Top 10 holders too concentrated ({token.top10_holders_pct:.1f}%)"

        if token.sniper_wallets > self.max_sniper_wallets:
            return False, f"Too many sniper wallets ({token.sniper_wallets})"

        return True, "Passed all filters
