"""Public Binance spot candles, used only when the bot's exchange has none.

No API key. This is market data for paper signals, not an order path.
Binance's public data site lists SOLUSDT and HYPEUSDT.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

PUBLIC_KLINE_URL = "https://data-api.binance.vision/api/v3/klines"

_INTERVALS = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "1d": "1d",
}


def spot_symbol(symbol: str) -> str | None:
    """Turn SOL/USDT:USDT or SOL/USDT into SOLUSDT."""
    if "/" not in symbol:
        return None
    base, quote = symbol.split("/", 1)
    quote = quote.split(":")[0]
    if not base or not quote:
        return None
    return f"{base}{quote}"


def parse_binance_klines(rows: list) -> list[list]:
    """Binance kline rows to [time, open, high, low, close, volume]."""
    candles = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        candles.append(
            [
                int(row[0]),
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
            ]
        )
    return candles


def fetch_public_klines(symbol: str, timeframe: str = "15m", limit: int = 200) -> list[list]:
    pair = spot_symbol(symbol)
    interval = _INTERVALS.get(timeframe)
    if not pair or not interval:
        return []
    safe_limit = min(max(int(limit), 1), 1000)
    query = urllib.parse.urlencode({"symbol": pair, "interval": interval, "limit": safe_limit})
    request = urllib.request.Request(
        f"{PUBLIC_KLINE_URL}?{query}",
        headers={"User-Agent": "trading-system", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode())
    if not isinstance(payload, list):
        return []
    return parse_binance_klines(payload)
