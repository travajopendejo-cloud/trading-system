"""Split the paper portfolio between the existing bots and the meme sniper."""

from __future__ import annotations

import copy
from typing import Any

DEFAULT_SNIPER_SHARE = 0.20
MIN_SNIPER_SHARE = 0.10
MAX_SNIPER_SHARE = 0.30

DISPLAY_NAMES = {
    "trend_bot": "Trend",
    "mean_reversion_bot": "Mean reversion",
    "breakout_bot": "Breakout",
    "ensemble_trend": "Ensemble trend",
    "swing": "Swing",
    "meme_sniper": "Meme sniper",
}


def clamp_sniper_share(value: Any) -> float:
    """Keep the meme sniper between 10% and 30%. 20 and 0.20 both mean 20%."""
    if value is None or value == "":
        number = DEFAULT_SNIPER_SHARE
    else:
        if isinstance(value, str):
            value = value.strip().rstrip("%").strip()
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = DEFAULT_SNIPER_SHARE
        if number > 1:
            number = number / 100.0
    return min(MAX_SNIPER_SHARE, max(MIN_SNIPER_SHARE, number))


def portfolio_allocations(
    initial_equity: float,
    bot_weights: dict[str, float],
    sniper_share: float = DEFAULT_SNIPER_SHARE,
    sniper_enabled: bool = True,
) -> list[dict[str, Any]]:
    """Dollar slices of the portfolio.

    The meme sniper takes its share first (10% to 30%). The other bots split
    whatever is left, using the same weights they already have in config.
    """
    equity = float(initial_equity)
    share = clamp_sniper_share(sniper_share) if sniper_enabled else 0.0
    rest = 1.0 - share
    weights = {
        name: float(weight)
        for name, weight in bot_weights.items()
        if name != "meme_sniper" and float(weight) > 0
    }
    weight_sum = sum(weights.values()) or 1.0
    rows: list[dict[str, Any]] = []
    for name, weight in weights.items():
        fraction = rest * (weight / weight_sum)
        rows.append(_row(name, fraction, equity))
    if sniper_enabled:
        rows.append(_row("meme_sniper", share, equity))
    return rows


def scale_sniper_config(config: dict[str, Any], allocated_usd: float, share: float) -> dict[str, Any]:
    """Turn the sniper's $100 paper shape into limits for its real portfolio slice.

    Position size and the daily loss limit grow in proportion to the dollars.
    Dollar limits grow with the allocated dollars. The open-position count stays
    at the configured cap. The result is always paper mode.
    """
    scaled = copy.deepcopy(config)
    scaled["mode"] = "paper"
    capital = dict(scaled.get("capital") or {})
    reference = float(capital.get("initial", 100) or 100)
    if reference <= 0:
        reference = 100.0
    allocated = max(0.0, float(allocated_usd))
    growth = allocated / reference if allocated else 1.0
    clamp_sniper_share(share)
    # The open-position count is a hard cap from config, not something that grows with the share.
    positions = max(1, int(capital.get("max_open_positions", 2) or 2))
    capital["initial"] = round(allocated, 2)
    capital["max_position_usd"] = round(float(capital.get("max_position_usd", 10)) * growth, 2)
    capital["daily_loss_limit_usd"] = round(float(capital.get("daily_loss_limit_usd", 20)) * growth, 2)
    capital["max_open_positions"] = positions
    scaled["capital"] = capital
    return scaled


def _row(key: str, fraction: float, equity: float) -> dict[str, Any]:
    return {
        "key": key,
        "name": DISPLAY_NAMES.get(key, key.replace("_", " ").title()),
        "share": fraction,
        "pct": fraction * 100.0,
        "usd": equity * fraction,
    }
