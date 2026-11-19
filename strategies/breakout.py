"""Breakout bot: close outside the prior channel, with volume and a real move."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.indicators import atr, ema


class BreakoutStrategy(BaseStrategy):
    """Enter when price closes beyond the previous channel, not this candle's own high.

    The channel is shifted by one bar. A close can never be above a high that
    already includes itself, so the old comparison almost never traded.
    """

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        period = int(self.params.get("donchian_period", self.params.get("channel_period", 20)))
        vol_period = int(self.params.get("volume_ma_period", 20))
        atr_period = int(self.params.get("atr_period", 14))
        atr_ma_period = int(self.params.get("atr_ma_period", 50))
        ema_period = int(self.params.get("ema_period", 50))
        out = df.copy()
        # Prior bars only. The current high is not allowed to count as the level it must break.
        out["channel_high"] = out["high"].rolling(window=period).max().shift(1)
        out["channel_low"] = out["low"].rolling(window=period).min().shift(1)
        out["volume_ma"] = out["volume"].rolling(window=vol_period).mean()
        out["atr"] = atr(out["high"], out["low"], out["close"], atr_period)
        out["atr_ma"] = out["atr"].rolling(window=atr_ma_period).mean()
        out["ema"] = ema(out["close"], ema_period)
        return out

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 70:
            return Signal(action="hold")

        prepared = self.prepare(df)
        row = prepared.iloc[-1]
        prev = prepared.iloc[-2]
        required = ["channel_high", "channel_low", "atr", "atr_ma", "volume_ma", "ema"]
        if any(pd.isna(row[col]) or pd.isna(prev[col]) for col in required):
            return Signal(action="hold")
        if row["atr"] <= 0 or row["close"] <= 0 or row["volume_ma"] <= 0:
            return Signal(action="hold")

        vol_mult = float(self.params.get("volume_mult", 1.5))
        atr_expansion = float(self.params.get("atr_expansion", 1.25))
        stop_mult = float(self.params.get("stop_loss_atr_mult", 2.0))
        target_mult = float(self.params.get("take_profit_atr_mult", 3.0))
        min_atr_pct = float(self.params.get("min_atr_pct", 0.3))

        atr_pct = (row["atr"] / row["close"]) * 100
        if atr_pct < min_atr_pct:
            return Signal(action="hold")
        if row["volume"] <= row["volume_ma"] * vol_mult:
            return Signal(action="hold")
        if row["atr"] <= row["atr_ma"] * atr_expansion:
            return Signal(action="hold")

        broke_up = prev["close"] <= prev["channel_high"] and row["close"] > row["channel_high"]
        broke_down = prev["close"] >= prev["channel_low"] and row["close"] < row["channel_low"]
        volume_ratio = float(row["volume"] / row["volume_ma"])

        if broke_up and row["close"] > row["ema"]:
            return Signal(
                action="buy",
                strength=0.85,
                stop_loss=float(row["close"] - stop_mult * row["atr"]),
                take_profit=float(row["close"] + target_mult * row["atr"]),
                meta={"type": "volatility_breakout_long", "atr_pct": float(atr_pct), "volume_ratio": volume_ratio},
            )
        if broke_down and row["close"] < row["ema"]:
            return Signal(
                action="sell",
                strength=0.85,
                stop_loss=float(row["close"] + stop_mult * row["atr"]),
                take_profit=float(row["close"] - target_mult * row["atr"]),
                meta={"type": "volatility_breakout_short", "atr_pct": float(atr_pct), "volume_ratio": volume_ratio},
            )
        return Signal(action="hold")
