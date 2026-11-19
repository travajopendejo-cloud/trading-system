"""Tracked wallets and the decision to copy one of their buys.

Addresses were checked on Solana mainnet on 2026-11-19 with the public RPC
(account exists, SOL balance, token accounts) plus public tracker pages.
Wallets that did not show real meme-coin trading were disabled.
No replacement addresses were added.

Kimchi (@kimchi1x) was reviewed in the strategy report and is not on this list.
The three active names below still need a fresh check for side wallets and
for an average hold longer than 30 minutes. Until that hold time is measured,
the copier will not follow them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


@dataclass
class TrackedWallet:
    address: str
    name: str
    notes: str = ""
    max_copy_usd: float = 80.0
    enabled: bool = True
    # True when the account exists and shows meme-coin trading activity.
    checks_out: bool = False
    # None means we have not measured it. Copying requires more than 30 minutes.
    avg_hold_minutes: float | None = None
    needs_reverification: bool = False


@dataclass
class CopySignal:
    wallet_name: str
    wallet_address: str
    token_address: str
    token_symbol: str
    entry_price: float
    amount_usd: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""


TRACKED_WALLETS: list[TrackedWallet] = [
    TrackedWallet(
        address="CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",
        name="Cented",
        notes=(
            "Checked on Solana on Nov 19, 2026. The account exists, holds about 910 SOL, "
            "and holds dozens of real-size pump.fun tokens. Public pages (Defined.fi, Vybe, "
            "KolQuest) label this address as Cented. Kept on."
        ),
        max_copy_usd=90.0,
        enabled=True,
        checks_out=True,
        needs_reverification=True,
    ),
    TrackedWallet(
        address="4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9",
        name="Decu",
        notes=(
            "Checked on Solana on Nov 19, 2026. The account exists, holds about 600 SOL, "
            "and holds nearly 200 real-size pump.fun tokens. Public pages label it as Decu "
            "(@notdecu). Kept on."
        ),
        max_copy_usd=85.0,
        enabled=True,
        checks_out=True,
        needs_reverification=True,
    ),
    TrackedWallet(
        address="5dzH7gh5FjtrxUwtfBufJyTBA4fyCUGheZsdYQsE9vag",
        name="Hermes",
        notes=(
            "Checked on Solana on Nov 19, 2026. The account exists, holds dozens of pump.fun "
            "tokens, and signed a buy that spent about $200 USDC on a pump.fun coin. pump.fun "
            "has a profile for this address. Kept on. That profile says not to copy him; this "
            "bot only paper-trades and still runs the safety filters."
        ),
        max_copy_usd=80.0,
        enabled=True,
        checks_out=True,
        needs_reverification=True,
    ),
    TrackedWallet(
        address="93WbteZH6nrWeHd5JT6mJE5VHbWcCTDZzzqqnpZ98V9G",
        name="Naseem",
        notes=(
            "Turned off on Nov 19, 2026. The account exists, but it is not an active meme "
            "trader. Bubblemaps linked it to Naseem because it once held naseem.sol and "
            "touched wallets from the TRUMP trade. The last activity found was September 4, "
            "2026, and those transactions were not meme buys. It had one small pump.fun "
            "balance. No replacement address was added."
        ),
        max_copy_usd=100.0,
        enabled=False,
        checks_out=False,
    ),
    TrackedWallet(
        address="9X5n5i1mugTjgGPhqf1KJDt8r4kD8TF3s62ttbxKzFHa",
        name="Dingaling",
        notes=(
            "Left off on purpose. The account exists and public trackers label it dingaling, "
            "and it does hold meme tokens. The newest transactions we could read were 1-token "
            "airdrops, not buys he paid for. It was already disabled. No replacement address "
            "was added."
        ),
        max_copy_usd=60.0,
        enabled=False,
        checks_out=True,
    ),
]


class WalletCopyManager:
    def __init__(self, risk_manager=None, wallets: list[TrackedWallet] | None = None):
        source = TRACKED_WALLETS if wallets is None else wallets
        self.wallets = {w.address: w for w in source if w.enabled}
        self.risk = risk_manager
        self.recent_signals: list[CopySignal] = []

    def update_wallet_list(self, new_wallets: list[TrackedWallet]) -> None:
        self.wallets = {w.address: w for w in new_wallets if w.enabled}
        logger.info("Updated tracked wallets. Now tracking %d wallets.", len(self.wallets))

    def should_copy(
        self,
        wallet_address: str,
        token_address: str,
        token_symbol: str,
        entry_price: float,
        size_usd: float,
        extra_info: dict | None = None,
        min_wallet_buy_usd: float = 25.0,
    ) -> CopySignal | None:
        """Decide whether a tracked wallet's buy is large enough to paper-copy.

        size_usd is what the wallet spent, not what we spend. Our paper size
        stays inside the sniper's normal position limit.
        """
        del extra_info  # kept so older callers do not crash
        wallet = self.wallets.get(wallet_address)
        if wallet is None:
            return None
        if entry_price <= 0 or size_usd < min_wallet_buy_usd:
            return None
        hold = wallet.avg_hold_minutes
        if hold is None or hold <= 30:
            logger.info(
                "Not copying %s. Average hold is %s; need more than 30 minutes, and the wallet still needs a fresh check.",
                wallet.name,
                "unknown" if hold is None else f"{hold:.0f} minutes",
            )
            return None

        copy_size = float(wallet.max_copy_usd)
        if self.risk is not None:
            copy_size = min(copy_size, float(self.risk.position_size()))
        if copy_size < 1:
            return None

        signal = CopySignal(
            wallet_name=wallet.name,
            wallet_address=wallet_address,
            token_address=token_address,
            token_symbol=token_symbol,
            entry_price=entry_price,
            amount_usd=copy_size,
            reason=f"Following {wallet.name} | wallet spent about ${size_usd:.0f}",
        )
        self.recent_signals.append(signal)
        logger.info(
            "COPY SIGNAL -> %s bought %s | paper size $%.0f",
            wallet.name,
            token_symbol,
            copy_size,
        )
        return signal

    def get_recent_signals(self, limit: int = 20) -> list[CopySignal]:
        return self.recent_signals[-limit:]
