"""Pure parsers for DexScreener, pump.fun, Solana RPC, and Helius payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from meme_sniper.core.constants import QUOTE_MINTS, USDC, USDT, WSOL
from meme_sniper.core.filters import TokenInfo

BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def looks_like_mint(value: str) -> bool:
    return isinstance(value, str) and 32 <= len(value) <= 44 and set(value) <= BASE58


def normalize_price_impact_pct(value: Any) -> float | None:
    """Jupiter returns a fraction such as 0.0108 for about 1.08% impact."""
    if value is None or value == "":
        return None
    try:
        number = abs(float(value))
    except (TypeError, ValueError):
        return None
    if number <= 1:
        return number * 100.0
    return number


@dataclass
class ObservedBuy:
    wallet_address: str
    token_address: str
    token_amount: float
    quote_mint: str
    quote_amount: float
    signature: str
    timestamp: datetime | None = None


def parse_dexscreener_profiles(payload: Any) -> list[str]:
    rows = payload if isinstance(payload, list) else []
    found: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("chainId") != "solana":
            continue
        address = row.get("tokenAddress")
        if looks_like_mint(address):
            found.append(address)
    return found


def parse_pump_coins(payload: Any) -> list[str]:
    rows: Any
    if isinstance(payload, dict):
        rows = payload.get("coins") or payload.get("data") or payload.get("results") or []
    else:
        rows = payload
    if not isinstance(rows, list):
        return []
    found: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        address = row.get("mint") or row.get("address") or row.get("tokenAddress")
        if looks_like_mint(address):
            found.append(address)
    return found


def token_info_from_dexscreener(
    payload: Any,
    mint: str,
    max_age_seconds: float | None = None,
    now: datetime | None = None,
) -> TokenInfo | None:
    pairs = payload.get("pairs") if isinstance(payload, dict) else None
    if not isinstance(pairs, list):
        return None
    usable = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        if pair.get("chainId") not in (None, "solana"):
            continue
        base = (pair.get("baseToken") or {}).get("address")
        quote = (pair.get("quoteToken") or {}).get("address")
        if mint not in (base, quote):
            continue
        usable.append(pair)
    if not usable:
        return None
    usable.sort(key=lambda pair: float((pair.get("liquidity") or {}).get("usd") or 0), reverse=True)
    best = usable[0]
    moment = now or datetime.now(timezone.utc)
    age_seconds = 0.0
    created = best.get("pairCreatedAt")
    if created:
        try:
            created_at = datetime.fromtimestamp(float(created) / 1000.0, timezone.utc)
            age_seconds = max(0.0, (moment - created_at).total_seconds())
        except (TypeError, ValueError, OSError):
            age_seconds = 0.0
    if max_age_seconds is not None and created and age_seconds > max_age_seconds:
        return None
    base_token = best.get("baseToken") or {}
    quote_token = best.get("quoteToken") or {}
    if base_token.get("address") == mint:
        symbol = base_token.get("symbol") or mint[:6]
        name = base_token.get("name") or symbol
    else:
        symbol = quote_token.get("symbol") or mint[:6]
        name = quote_token.get("name") or symbol
    try:
        price = float(best.get("priceUsd") or 0)
    except (TypeError, ValueError):
        price = 0.0
    try:
        liquidity = float((best.get("liquidity") or {}).get("usd") or 0)
    except (TypeError, ValueError):
        liquidity = 0.0
    return TokenInfo(
        symbol=str(symbol)[:24] or mint[:6],
        address=mint,
        price=price,
        liquidity_usd=liquidity,
        age_seconds=age_seconds,
        name=str(name)[:48],
        source="dexscreener",
    )


def _ui_amount(raw: dict[str, Any]) -> float:
    try:
        amount = int(str(raw.get("tokenAmount")))
        decimals = int(raw.get("decimals") or 0)
    except (TypeError, ValueError):
        return 0.0
    if decimals < 0:
        return 0.0
    return amount / float(10 ** decimals)


def _quote_is_meaningful(quote_mint: str, quote_amount: float) -> bool:
    if quote_mint in (USDC, USDT):
        return quote_amount >= 1.0
    if quote_mint == WSOL:
        return quote_amount >= 0.01
    return quote_amount > 0


def _choose_quote(spent: list[tuple[str, float]]) -> tuple[str, float] | None:
    stables = [(mint, amount) for mint, amount in spent if mint in (USDC, USDT)]
    sols = [(mint, amount) for mint, amount in spent if mint == WSOL]
    if stables:
        mint = stables[0][0]
        return mint, sum(amount for _, amount in stables)
    if sols:
        return WSOL, sum(amount for _, amount in sols)
    return None


def _timestamp_from(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _buy_from_nets(
    wallet: str,
    nets: dict[str, float],
    signature: str,
    timestamp: datetime | None,
    native_sol_spent: float = 0.0,
) -> list[ObservedBuy]:
    bought = [(mint, amount) for mint, amount in nets.items() if mint not in QUOTE_MINTS and amount > 1]
    spent = [(mint, -amount) for mint, amount in nets.items() if mint in QUOTE_MINTS and amount < 0]
    if native_sol_spent > 0:
        spent.append((WSOL, native_sol_spent))
    if not bought:
        return []
    quote = _choose_quote(spent)
    if quote is None or not _quote_is_meaningful(*quote):
        return []
    token_mint, token_amount = max(bought, key=lambda item: item[1])
    quote_mint, quote_amount = quote
    return [
        ObservedBuy(
            wallet_address=wallet,
            token_address=token_mint,
            token_amount=token_amount,
            quote_mint=quote_mint,
            quote_amount=quote_amount,
            signature=signature,
            timestamp=timestamp,
        )
    ]


def parse_wallet_transaction(tx: dict[str, Any] | None, wallet: str, signature: str = "") -> list[ObservedBuy]:
    """Parse a jsonParsed getTransaction result. Only signer buys are returned."""
    if not isinstance(tx, dict):
        return []
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return []
    message = ((tx.get("transaction") or {}).get("message") or {})
    signer = False
    for key in message.get("accountKeys") or []:
        if isinstance(key, dict) and key.get("pubkey") == wallet and key.get("signer"):
            signer = True
            break
    if not signer:
        return []

    def balances(rows: Any) -> dict[str, float]:
        totals: dict[str, float] = {}
        for row in rows or []:
            if not isinstance(row, dict) or row.get("owner") != wallet:
                continue
            mint = row.get("mint")
            if not isinstance(mint, str):
                continue
            try:
                amount = float((row.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            totals[mint] = totals.get(mint, 0.0) + amount
        return totals

    before = balances(meta.get("preTokenBalances"))
    after = balances(meta.get("postTokenBalances"))
    nets: dict[str, float] = {}
    for mint in set(before) | set(after):
        delta = after.get(mint, 0.0) - before.get(mint, 0.0)
        if abs(delta) > 1e-9:
            nets[mint] = delta
    sigs = (tx.get("transaction") or {}).get("signatures") or []
    sig = signature or (sigs[0] if sigs else "") or str(tx.get("signature") or "")
    return _buy_from_nets(wallet, nets, sig, _timestamp_from(tx.get("blockTime")))


def parse_helius_swap(tx: dict[str, Any] | None, wallet: str) -> list[ObservedBuy]:
    """Parse one Helius enhanced transaction. Sells and airdrops are ignored."""
    if not isinstance(tx, dict):
        return []
    if tx.get("transactionError") or tx.get("err"):
        return []
    kind = tx.get("type")
    if kind not in (None, "SWAP", "TRANSFER"):
        return []
    fee_payer = tx.get("feePayer")
    if fee_payer and fee_payer != wallet:
        return []
    signature = str(tx.get("signature") or "")
    timestamp = _timestamp_from(tx.get("timestamp") or tx.get("blockTime"))
    swap = (tx.get("events") or {}).get("swap") if isinstance(tx.get("events"), dict) else None
    nets: dict[str, float] = {}
    native_sol = 0.0
    if isinstance(swap, dict):
        for row in swap.get("tokenInputs") or []:
            if not isinstance(row, dict):
                continue
            user = row.get("userAccount") or fee_payer
            if user not in (None, wallet):
                continue
            mint = row.get("mint")
            if isinstance(mint, str):
                nets[mint] = nets.get(mint, 0.0) - _ui_amount(row.get("rawTokenAmount") or {})
        for row in swap.get("tokenOutputs") or []:
            if not isinstance(row, dict):
                continue
            user = row.get("userAccount") or fee_payer
            if user not in (None, wallet):
                continue
            mint = row.get("mint")
            if isinstance(mint, str):
                nets[mint] = nets.get(mint, 0.0) + _ui_amount(row.get("rawTokenAmount") or {})
        native = swap.get("nativeInput") or {}
        if isinstance(native, dict) and native.get("amount"):
            try:
                native_sol = int(str(native.get("amount"))) / 1_000_000_000
            except (TypeError, ValueError):
                native_sol = 0.0
    if not nets:
        for transfer in tx.get("tokenTransfers") or []:
            if not isinstance(transfer, dict):
                continue
            mint = transfer.get("mint")
            if not isinstance(mint, str):
                continue
            try:
                amount = float(transfer.get("tokenAmount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if transfer.get("toUserAccount") == wallet:
                nets[mint] = nets.get(mint, 0.0) + amount
            if transfer.get("fromUserAccount") == wallet:
                nets[mint] = nets.get(mint, 0.0) - amount
    return _buy_from_nets(wallet, nets, signature, timestamp, native_sol_spent=native_sol)
