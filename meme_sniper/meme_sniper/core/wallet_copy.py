"""
Wallet Copy Module
Tracks selected high-performing wallets and decides whether to copy their buys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class TrackedWallet:
    address: str
    name: str
    notes: str = ""
    max_copy_usd: float = 80.0          # Max we are willing to risk following this wallet
    enabled: bool = True


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


# Real addresses of strong current / known traders
TRACKED_WALLETS: List[TrackedWallet] = [
    TrackedWallet(
        address="CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",
        name="Cented",
        notes="Consistently top performer. High volume, solid long-term results.",
        max_copy_usd=90.0
    ),
    TrackedWallet(
        address="4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9",
        name="Decu",
        notes="High win rate trader, currently ranking well on multiple leaderboards.",
        max_copy_usd=85.0
    ),
    TrackedWallet(
        address="5dzH7gh5FjtrxUwtfBufJyTBA4fyCUGheZsdYQsE9vag",
        name="Hermes",
        notes="Frequently tops daily and weekly PnL boards.",
        max_copy_usd=80.0
    ),
    TrackedWallet(
        address="93WbteZH6nrWeHd5JT6mJE5VHbWcCTDZzzqqnpZ98V9G",
        name="Naseem",
        notes="Big $TRUMP winner (linked by Bubblemaps). Uses multiple wallets.",
        max_copy_usd=100.0
    ),
    TrackedWallet(
        address="9X5n5i1mugTjgGPhqf1KJDt8r4kD8TF3s62ttbxKzFHa",
        name="Dingaling",
        notes="Interesting name. More active on ETH; lower confidence on pure Solana sniping.",
        max_copy_usd=60.0,
        enabled=False  # Turn on only if you want him
    ),
]


class WalletCopyManager:
    def __init__(self, risk_manager=None):
        self.wallets = {w.address: w for w in TRACKED_WALLETS if w.enabled}
        self.risk = risk_manager
        self.recent_signals: List[CopySignal] = []

    def update_wallet_list(self, new_wallets: List[TrackedWallet]):
        """Allow updating the list of tracked wallets."""
        self.wallets = {w.address: w for w in new_wallets if w.enabled}
        logger.info(f"Updated tracked wallets. Now tracking {len(self.wallets)} wallets.")

    def should_copy(
        self,
        wallet_address: str,
        token_address: str,
        token_symbol: str,
        entry_price: float,
        size_usd: float,
        extra_info: dict | None = None,
    ) -> Optional[CopySignal]:
        """
        Decide whether we should copy a buy from a tracked wallet.
        Returns a CopySignal if we decide to copy, otherwise None.
        """
        wallet = self.wallets.get(wallet_address)
        if not wallet:
            return None

        # Basic size limit per wallet
        copy_size = min(size_usd * 0.15, wallet.max_copy_usd)

        if copy_size < 15:  # Too small to bother
            return None

        signal = CopySignal(
            wallet_name=wallet.name,
            wallet_address=wallet_address,
            token_address=token_address,
            token_symbol=token_symbol,
            entry_price=entry_price,
            amount_usd=copy_size,
            reason=f"Following {wallet.name} | Original size ~${size_usd:.0f}"
        )

        self.recent_signals.append(signal)
        logger.info(
            f"COPY SIGNAL → {wallet.name} bought {token_symbol} | "
            f"We take ${copy_size:.0f}"
        )
        return signal

    def get_recent_signals(self, limit: int = 20) -> List[CopySignal]:
        return self.recent_signals[-limit:]
