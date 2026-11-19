"""Safety filters that must pass before any paper buy."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    top_holder_pct: float = 0.0
    sniper_wallets: int = 0
    age_seconds: float = 0.0
    name: str = ""
    source: str = ""
    dex: str = ""
    graduated: bool = False
    pair_address: str = ""
    buy_count: int = 0
    sell_count: int = 0
    unique_buyers: int | None = None
    lp_secured: bool | None = None
    dev_checked: bool = False
    sell_tax_pct: float | None = None
    decimals: int = 6
    supply_ui: float = 0.0
    # None means "we could not check". False means the check failed.
    mint_revoked: bool | None = None
    freeze_revoked: bool | None = None
    holder_checked: bool = False
    sell_route_ok: bool | None = None
    sell_price_impact_pct: float | None = None
    safety_checked: bool = False
    safety_note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class TokenFilter:
    def __init__(self, config: dict[str, Any]):
        filters = config.get("filters", {}) or {}
        self.max_dev_buy_pct = float(filters.get("max_dev_buy_pct", 8.0))
        self.max_bundle_pct = float(filters.get("max_bundle_pct", 12.0))
        self.max_top10_holders_pct = float(filters.get("max_top10_holders_pct", 70.0))
        self.max_top_holder_pct = float(filters.get("max_top_holder_pct", 45.0))
        self.min_liquidity_usd = float(filters.get("min_liquidity_usd", 800))
        self.max_sniper_wallets = int(filters.get("max_sniper_wallets", 15))
        self.max_price_impact_pct = float(filters.get("max_price_impact_pct", 25.0))
        self.require_safety_check = bool(filters.get("require_safety_check", True))
        self.require_sell_route = bool(filters.get("require_sell_route", True))
        self.require_holder_data = bool(filters.get("require_holder_data", False))
        self.require_graduated = bool(filters.get("require_graduated", False))
        self.min_graduated_age_seconds = float(filters.get("min_graduated_age_seconds", 0))
        self.require_lp_lock = bool(filters.get("require_lp_lock", False))
        self.require_dev_check = bool(filters.get("require_dev_check", False))
        self.require_two_sided_volume = bool(filters.get("require_two_sided_volume", False))
        self.min_unique_buyers = int(filters.get("min_unique_buyers", 0))
        self.max_sell_tax_pct = float(filters.get("max_sell_tax_pct", 100))

    def is_valid(self, token: TokenInfo) -> tuple[bool, str]:
        """Return (allowed, reason). A buy is allowed only when the reason is a pass."""
        if token.price <= 0:
            return False, "No usable price"

        if self.require_safety_check and not token.safety_checked:
            note = f" ({token.safety_note})" if token.safety_note else ""
            return False, f"Safety checks not completed{note}"

        if token.mint_revoked is not True:
            return False, "Mint authority is still on, so more tokens can be created"

        if token.freeze_revoked is not True:
            return False, "Freeze authority is on, so sells can be frozen"

        if self.require_graduated and not token.graduated:
            return False, "Not a graduated pump.fun token"

        if self.require_graduated and token.age_seconds < self.min_graduated_age_seconds:
            return False, "Graduated less than 30 minutes ago"

        if self.require_lp_lock and token.lp_secured is not True:
            return False, "LP is not locked or burned" if token.lp_secured is False else "LP lock or burn could not be verified"

        if self.require_dev_check and not token.dev_checked:
            return False, "Dev wallet size was not checked"

        if token.dev_checked and token.dev_buy_pct > self.max_dev_buy_pct:
            return False, f"Dev wallet too large ({token.dev_buy_pct:.1f}%)"

        if self.require_two_sided_volume and (token.buy_count < 1 or token.sell_count < 1):
            return False, "Needs both buy and sell volume"

        if self.min_unique_buyers and (token.unique_buyers is None or token.unique_buyers < self.min_unique_buyers):
            return False, "Not enough distinct buyers"

        if token.sell_tax_pct is not None and token.sell_tax_pct > self.max_sell_tax_pct:
            return False, f"Sell tax or shortfall too high ({token.sell_tax_pct:.1f}%)"

        if token.liquidity_usd < self.min_liquidity_usd:
            return False, f"Liquidity too low (${token.liquidity_usd:.0f})"

        if token.bundle_pct > self.max_bundle_pct:
            return False, f"Bundle concentration too high ({token.bundle_pct:.1f}%)"

        if self.require_holder_data and not token.holder_checked:
            return False, "Holder concentration could not be checked"

        if token.holder_checked and token.top_holder_pct > self.max_top_holder_pct:
            return False, (
                f"Top holder too concentrated ({token.top_holder_pct:.1f}%)"
            )

        if token.holder_checked and token.top10_holders_pct > self.max_top10_holders_pct:
            return False, (
                f"Top 10 holders too concentrated ({token.top10_holders_pct:.1f}%)"
            )

        if token.sniper_wallets > self.max_sniper_wallets:
            return False, f"Too many sniper wallets ({token.sniper_wallets})"

        if token.sell_route_ok is False:
            return False, "No sell route (honeypot-style check failed)"

        if self.require_sell_route and token.sell_route_ok is not True:
            return False, "Sell route could not be confirmed"

        impact = token.sell_price_impact_pct
        if impact is not None and impact > self.max_price_impact_pct:
            return False, f"Sell price impact too high ({impact:.1f}%)"

        return True, "Passed all filters"
