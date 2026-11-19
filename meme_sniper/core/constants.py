"""Shared Solana mints and addresses used by the meme sniper."""

from __future__ import annotations

# Wrapped SOL, USDC, and USDT. These are quote assets, not meme coins.
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTE_MINTS = {WSOL, USDC, USDT}

# Accounts that hold burned tokens. They are not real holders.
BURN_OWNERS = {
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
}

DEFAULT_PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
PUMP_LATEST_URL = (
    "https://frontend-api-v3.pump.fun/coins/latest?limit=30&offset=0&includeNsfw=false"
)
DEXSCREENER_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
JUPITER_QUOTE_URL = "https://api.jup.ag/swap/v1/quote"
HELIUS_TX_URL = "https://api.helius.xyz/v0/addresses/{address}/transactions"
