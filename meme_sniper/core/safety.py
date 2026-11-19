"""On-chain rug checks: mint authority, freeze authority, holders, and a sell quote."""

from __future__ import annotations

import logging
from typing import Any

from meme_sniper.core.constants import BURN_OWNERS, JUPITER_QUOTE_URL, WSOL
from meme_sniper.core.filters import TokenInfo
from meme_sniper.core.http_client import HttpJson, PermanentFetchError, TemporaryFetchError
from meme_sniper.core.parsers import normalize_price_impact_pct

logger = logging.getLogger(__name__)


class SafetyChecker:
    def __init__(self, http: HttpJson, rpc_url: str) -> None:
        self.http = http
        self.rpc_url = rpc_url

    async def enrich(self, token: TokenInfo) -> TokenInfo:
        """Fill safety fields. A missing mint is treated as unsafe, not as a pass."""
        try:
            result = await self._rpc(
                "getAccountInfo",
                [token.address, {"encoding": "jsonParsed"}],
            )
        except TemporaryFetchError as exc:
            token.safety_checked = False
            token.safety_note = str(exc)
            return token

        value = (result or {}).get("value") if isinstance(result, dict) else None
        parsed = {}
        if isinstance(value, dict) and isinstance(value.get("data"), dict):
            parsed = ((value["data"].get("parsed") or {}).get("info") or {})
        if not parsed:
            token.safety_checked = True
            token.mint_revoked = False
            token.freeze_revoked = False
            token.safety_note = "Mint account not found or unreadable"
            return token

        try:
            token.decimals = int(parsed.get("decimals") or 0)
        except (TypeError, ValueError):
            token.decimals = 0
        token.mint_revoked = parsed.get("mintAuthority") in (None, "")
        token.freeze_revoked = parsed.get("freezeAuthority") in (None, "")
        try:
            supply_raw = float(parsed.get("supply") or 0)
        except (TypeError, ValueError):
            supply_raw = 0.0
        token.supply_ui = supply_raw / float(10 ** token.decimals) if token.decimals >= 0 else 0.0

        await self._fill_holders(token)
        await self._fill_sell_quote(token)
        token.safety_checked = True
        return token

    async def _fill_holders(self, token: TokenInfo) -> None:
        if token.supply_ui <= 0:
            return
        try:
            result = await self._rpc("getTokenLargestAccounts", [token.address])
        except (TemporaryFetchError, PermanentFetchError) as exc:
            logger.info("Holder check unavailable for %s: %s", token.symbol, exc)
            return
        rows = (result or {}).get("value") if isinstance(result, dict) else None
        if not isinstance(rows, list) or not rows:
            return
        sized: list[tuple[str, float]] = []
        for row in rows[:10]:
            if not isinstance(row, dict):
                continue
            try:
                amount = float(row.get("uiAmount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            address = row.get("address")
            if isinstance(address, str) and amount > 0:
                sized.append((address, amount))
        if not sized:
            return
        owners = await self._token_account_owners([address for address, _ in sized[:3]])
        amounts = []
        for address, amount in sized:
            owner = owners.get(address)
            if owner in BURN_OWNERS:
                continue
            amounts.append(amount)
        if not amounts:
            return
        amounts.sort(reverse=True)
        token.top_holder_pct = amounts[0] / token.supply_ui * 100.0
        token.top10_holders_pct = sum(amounts[:10]) / token.supply_ui * 100.0
        token.holder_checked = True

    async def _token_account_owners(self, addresses: list[str]) -> dict[str, str]:
        if not addresses:
            return {}
        try:
            result = await self._rpc(
                "getMultipleAccounts",
                [addresses, {"encoding": "jsonParsed"}],
            )
        except (TemporaryFetchError, PermanentFetchError):
            return {}
        values = (result or {}).get("value") if isinstance(result, dict) else None
        if not isinstance(values, list):
            return {}
        owners: dict[str, str] = {}
        for address, value in zip(addresses, values):
            if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
                continue
            info = ((value["data"].get("parsed") or {}).get("info") or {})
            owner = info.get("owner")
            if isinstance(owner, str):
                owners[address] = owner
        return owners

    async def _fill_sell_quote(self, token: TokenInfo) -> None:
        if token.price <= 0 or token.decimals < 0:
            token.sell_route_ok = False
            token.safety_note = token.safety_note or "Cannot price a sell quote"
            return
        raw = int((5.0 / token.price) * (10 ** token.decimals))
        raw = min(max(raw, 1), 10 ** 18)
        url = (
            f"{JUPITER_QUOTE_URL}?inputMint={token.address}"
            f"&outputMint={WSOL}&amount={raw}&slippageBps=100"
        )
        try:
            data = await self.http.get_json(url)
        except TemporaryFetchError as exc:
            token.sell_route_ok = None
            token.safety_note = token.safety_note or str(exc)
            return
        except PermanentFetchError:
            token.sell_route_ok = False
            return
        if not isinstance(data, dict) or data.get("error"):
            token.sell_route_ok = False
            return
        try:
            out_amount = int(str(data.get("outAmount") or "0"))
        except (TypeError, ValueError):
            out_amount = 0
        if out_amount <= 0:
            token.sell_route_ok = False
            return
        token.sell_route_ok = True
        token.sell_price_impact_pct = normalize_price_impact_pct(data.get("priceImpactPct"))

    async def _rpc(self, method: str, params: list[Any]) -> Any:
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
