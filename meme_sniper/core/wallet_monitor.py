"""Poll tracked wallets for new buys.

Helius is used when HELIUS_API_KEY is set. Otherwise the public Solana RPC
is used. If both fail, that wallet is skipped for the cycle. Nothing is raised,
and historical buys from before startup are not copied.
"""

from __future__ import annotations

import logging
from collections import deque

from meme_sniper.core.constants import HELIUS_TX_URL
from meme_sniper.core.http_client import HttpJson, TemporaryFetchError
from meme_sniper.core.parsers import ObservedBuy, parse_helius_swap, parse_wallet_transaction
from meme_sniper.core.wallet_copy import TrackedWallet

logger = logging.getLogger(__name__)


class WalletMonitor:
    def __init__(
        self,
        wallets: list[TrackedWallet],
        http: HttpJson,
        rpc_url: str,
        helius_api_key: str = "",
        max_signatures: int = 25,
        max_transactions: int = 8,
    ) -> None:
        self.wallets = [wallet for wallet in wallets if wallet.enabled]
        self.http = http
        self.rpc_url = rpc_url
        self.helius_api_key = (helius_api_key or "").strip()
        self.max_signatures = max(1, int(max_signatures))
        self.max_transactions = max(1, int(max_transactions))
        self.seen: set[str] = set()
        self.seen_order: deque[str] = deque()
        self.primed: set[str] = set()
        self.last_error: str | None = None

    async def prime(self) -> None:
        """Remember swaps that already exist so we only copy new ones."""
        for wallet in self.wallets:
            try:
                await self._mark_current(wallet.address)
            except Exception as exc:
                self.last_error = f"{wallet.name}: {exc}"
                logger.warning(
                    "Wallet %s was not primed (%s). It will not be copied until a later read works.",
                    wallet.name,
                    exc,
                )
                continue
            self.primed.add(wallet.address)

    async def poll(self) -> list[ObservedBuy]:
        buys: list[ObservedBuy] = []
        for wallet in self.wallets:
            if wallet.address not in self.primed:
                try:
                    await self._mark_current(wallet.address)
                except Exception as exc:
                    self.last_error = f"{wallet.name}: {exc}"
                    logger.warning("Wallet %s still unavailable: %s", wallet.name, exc)
                    continue
                self.primed.add(wallet.address)
                continue
            try:
                buys.extend(await self._new_buys(wallet.address))
            except Exception as exc:
                self.last_error = f"{wallet.name}: {exc}"
                logger.warning("Wallet %s poll failed: %s", wallet.name, exc)
        return buys

    async def _mark_current(self, address: str) -> None:
        if self.helius_api_key:
            try:
                for tx in await self._helius(address):
                    self._remember(str(tx.get("signature") or ""))
                return
            except Exception as exc:
                logger.warning("Helius prime failed (%s). Trying public RPC.", exc)
        for signature, _ok in await self._signature_rows(address):
            self._remember(signature)

    async def _new_buys(self, address: str) -> list[ObservedBuy]:
        if self.helius_api_key:
            try:
                return self._buys_from_helius(address, await self._helius(address))
            except Exception as exc:
                logger.warning("Helius poll failed (%s). Trying public RPC.", exc)
        fresh: list[str] = []
        for signature, ok in await self._signature_rows(address):
            if self._remember(signature) and ok:
                fresh.append(signature)
        buys: list[ObservedBuy] = []
        for signature in fresh[: self.max_transactions]:
            tx = await self._transaction(signature)
            if not isinstance(tx, dict):
                continue
            buys.extend(parse_wallet_transaction(tx, address, signature=signature))
        return buys

    def _buys_from_helius(self, address: str, txs: list[dict]) -> list[ObservedBuy]:
        buys: list[ObservedBuy] = []
        for tx in txs:
            signature = str(tx.get("signature") or "")
            if not self._remember(signature):
                continue
            buys.extend(parse_helius_swap(tx, address))
        return buys

    async def _helius(self, address: str) -> list[dict]:
        url = (
            HELIUS_TX_URL.format(address=address)
            + f"?api-key={self.helius_api_key}&type=SWAP&limit={self.max_signatures}"
        )
        data = await self.http.get_json(url)
        if isinstance(data, dict) and data.get("error"):
            raise TemporaryFetchError("Helius returned an error")
        if not isinstance(data, list):
            raise TemporaryFetchError("Helius returned an unexpected payload")
        return [row for row in data if isinstance(row, dict)]

    async def _signature_rows(self, address: str) -> list[tuple[str, bool]]:
        result = await self._rpc(
            "getSignaturesForAddress",
            [address, {"limit": self.max_signatures}],
        )
        if not isinstance(result, list):
            raise TemporaryFetchError("Signature list was empty or invalid")
        rows: list[tuple[str, bool]] = []
        for row in result:
            if not isinstance(row, dict):
                continue
            signature = row.get("signature")
            if isinstance(signature, str) and signature:
                rows.append((signature, not row.get("err")))
        return rows

    async def _transaction(self, signature: str) -> dict | None:
        result = await self._rpc(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}],
        )
        return result if isinstance(result, dict) else None

    async def _rpc(self, method: str, params: list) -> object:
        data = await self.http.post_json(
            self.rpc_url,
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        if not isinstance(data, dict):
            raise TemporaryFetchError("RPC returned no JSON object")
        if data.get("error"):
            message = data["error"]
            if isinstance(message, dict):
                message = message.get("message", message)
            raise TemporaryFetchError(str(message)[:180])
        return data.get("result")

    def _remember(self, signature: str) -> bool:
        if not signature or signature in self.seen:
            return False
        self.seen.add(signature)
        self.seen_order.append(signature)
        while len(self.seen_order) > 4000:
            self.seen.discard(self.seen_order.popleft())
        return True
