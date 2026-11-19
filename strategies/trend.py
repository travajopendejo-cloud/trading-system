"""Trend bot: fast/slow EMA cross, only when the trend is strong enough."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.indicators import adx, atr, ema


class TrendStrategy(BaseStrategy):
    """Buy when the fast average crosses above the slow one and ADX is high.

    The config already names fast_ema, slow_ema, and adx_threshold. A quiet
    market (low ADX) is skipped so the bot does not trade back-and-forth chop.
    """

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        fast = int(self.params.get("fast_ema", 12))
        slow = int(self.params.get("slow_ema", 26))
        adx_period = int(self.params.get("adx_period", 14))
        atr_period = int(self.params.get("atr_period", 14))
        out = df.copy()
        out["ema_fast"] = ema(out["close"], fast)
        out["ema_slow"] = ema(out["close"], slow)
        out["adx"] = adx(out["high"], out["low"], out["close"], adx_period)
        out["atr"] = atr(out["high"], out["low"], out["close"], atr_period)
        return out

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        slow = int(self.params.get("slow_ema", 26))
        if len(df) < max(60, slow + 5):
            return Signal(action="hold")

        prepared = self.prepare(df)
        row = prepared.iloc[-1]
        prev = prepared.iloc[-2]
        required = ["ema_fast", "ema_slow", "adx", "atr"]
        if any(pd.isna(row[col]) or pd.isna(prev[col]) for col in required):
            return Signal(action="hold")
        if row["atr"] <= 0 or row["close"] <= 0:
            return Signal(action="hold")

        adx_threshold = float(self.params.get("adx_threshold", 25.0))
        if row["adx"] < adx_threshold:
            return Signal(action="hold")

        stop_mult = float(self.params.get("stop_loss_atr_mult", 1.5))
        target_mult = float(self.params.get("take_profit_atr_mult", 2.5))
        crossed_up = prev["ema_fast"] <= prev["ema_slow"] and row["ema_fast"] > row["ema_slow"]
        crossed_down = prev["ema_fast"] >= prev["ema_slow"] and row["ema_fast"] < row["ema_slow"]
        strength = min(float(row["adx"]) / 50.0, 1.0)

        if crossed_up and row["close"] > row["ema_slow"]:
            return Signal(
                action="buy",
                strength=strength,
                stop_loss=float(row["close"] - stop_mult * row["atr"]),
                take_profit=float(row["close"] + target_mult * row["atr"]),
                meta={"type": "ema_cross_long", "adx": float(row["adx"]), "atr": float(row["atr"])},
            )
        if crossed_down and row["close"] < row["ema_slow"]:
            return Signal(
                action="sell",
                strength=strength,
                stop_loss=float(row["close"] + stop_mult * row["atr"]),
                take_profit=float(row["close"] - target_mult * row["atr"]),
                meta={"type": "ema_cross_short", "adx": float(row["adx"]), "atr": float(row["atr"])},
            )
        return Signal(action="hold")
