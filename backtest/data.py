"""Daily Binance spot candles, cached as CSV. No API key."""

from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PUBLIC_KLINE_URL = "https://data-api.binance.vision/api/v3/klines"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "klines"

# Listing dates used only as a download start. HYPE is discovered from the API.
STARTS = {
    "BTCUSDT": "2017-08-17",
    "ETHUSDT": "2017-08-17",
    "SOLUSDT": "2020-08-11",
    "HYPEUSDT": "2024-01-01",
}


def _ms(day: str) -> int:
    moment = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def fetch_daily(symbol: str, start: str, end: str | None = None) -> list[dict[str, float | str]]:
    """Download daily candles from start through end, inclusive when end is set."""
    cursor = _ms(start)
    stop = _ms(end) + 86_400_000 if end else None
    rows: list[dict[str, float | str]] = []
    while True:
        query = {"symbol": symbol, "interval": "1d", "startTime": cursor, "limit": 1000}
        if stop is not None:
            query["endTime"] = stop
        url = PUBLIC_KLINE_URL + "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={"User-Agent": "trading-system", "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
        if not isinstance(payload, list) or not payload:
            break
        for item in payload:
            stamp = int(item[0])
            if stop is not None and stamp > stop:
                continue
            day = datetime.fromtimestamp(stamp / 1000, timezone.utc).strftime("%Y-%m-%d")
            rows.append(
                {
                    "date": day,
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                }
            )
        cursor = int(payload[-1][0]) + 86_400_000
        if len(payload) < 1000:
            break
        if stop is not None and cursor > stop:
            break
    deduped = {row["date"]: row for row in rows}
    return [deduped[day] for day in sorted(deduped)]


def cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol}_1d.csv"


def save_csv(symbol: str, rows: list[dict]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(symbol)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def load_csv(symbol: str) -> list[dict]:
    path = cache_path(symbol)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    parsed = []
    for row in rows:
        parsed.append(
            {
                "date": row["date"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    return parsed


def frame_from_rows(rows: list[dict], start: str | None = None, end: str | None = None) -> dict[str, list]:
    chosen = [
        row
        for row in rows
        if (start is None or row["date"] >= start) and (end is None or row["date"] <= end)
    ]
    return {
        "date": [row["date"] for row in chosen],
        "high": [row["high"] for row in chosen],
        "low": [row["low"] for row in chosen],
        "close": [row["close"] for row in chosen],
    }


def ensure_cache(symbols: dict[str, str] | None = None, refresh: bool = False) -> dict[str, list[dict]]:
    """Load cached daily candles, downloading any symbol that is missing."""
    wanted = symbols or STARTS
    loaded = {}
    for symbol, start in wanted.items():
        path = cache_path(symbol)
        if path.exists() and not refresh:
            loaded[symbol] = load_csv(symbol)
            continue
        rows = fetch_daily(symbol, start)
        if not rows:
            raise RuntimeError(f"No daily candles downloaded for {symbol}")
        save_csv(symbol, rows)
        loaded[symbol] = rows
    return loaded
