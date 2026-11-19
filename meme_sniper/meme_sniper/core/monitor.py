"""Simple mock monitor that generates fake new tokens for testing."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone

from .filters import TokenInfo


class MockMonitor:
    def __init__(self):
        self.running = False

    async def stream_new_tokens(self):
        """Yield fake tokens every few seconds for testing."""
        self.running = True
        counter = 1

        while self.running:
            # Generate a somewhat realistic fake token
            price = random.uniform(0.000001, 0.00005)
            token = TokenInfo(
                symbol=f"MEME{counter}",
                address=f"TokenAddress{counter}pump",
                price=price,
                liquidity_usd=random.uniform(500, 8000),
                dev_buy_pct=random.uniform(1.0, 12.0),
                bundle_pct=random.uniform(2.0, 18.0),
                top10_holders_pct=random.uniform(15.0, 45.0),
                sniper_wallets=random.randint(3, 25),
                age_seconds=random.uniform(5, 40),
            )
            counter += 1
            yield token
            await asyncio.sleep(random.uniform(8, 15))  # new token every 8-15s
