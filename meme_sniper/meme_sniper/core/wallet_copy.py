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


# Starting list of wallets we want to follow
# You can expand this list later
TRACKED_WALLETS: List[TrackedWallet] = [
    TrackedWallet(
        address="Naseem_wallet_placeholder",   # Replace with real address later
        name="Naseem",
        notes="Big $TRUMP winner (~$53M). Very aggressive early entries.",
        max_copy_usd=100.0
    ),
    TrackedWallet(
        address="Dingaling_wallet_placeholder",
        name="Dingaling",
        notes="Interesting name, Boop.fun founder, solid $TRUMP profits.",
        max_copy_usd=70.0
    ),
    TrackedWallet(
        address="Hermes_wallet_placeholder",
        name="Hermes",
        notes="Currently one of the hottest wallets on leaderboards.",
        max_copy_usd=80.0
    ),
    TrackedWallet(
        address="Cented_wallet_placeholder",
        name="Cented",
        notes="Consistently top performer over many months.",
        max_copy_usd=90.0
    ),
    TrackedWallet(
        address="Decu_wallet_placeholder",
        name="Decu",
        notes="High win rate trader.",
        max_copy_usd=75.0
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
        copy_size = min(size_usd * 0.15, wallet.max_copy_usd)  # Copy only a fraction

        if copy_size < 15:  # Too small to bother
            return None

        # Here we can later add more advanced filters:
        # - Token age
        # - Liquidity
        # - Dev holdings
        # - Social signals
        # - Our own risk limits

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
