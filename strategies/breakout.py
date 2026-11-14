"""Breakout strategy: Donchian channel + volume filter."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.indicators import atr


class BreakoutStrategy(BaseStrategy):
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        period = int(self.params.get("donchian_period", 20))
        vol_period = int(self.params.get("volume_ma_period", 20))
        atr_period = int(self.params.get("atr_period", 14))

        df = df.copy()
        df["donchian_high"] = df["high"].rolling(window=period).max()
        df["donchian_low"] = df["low"].rolling(window=period).min()
        df["volume_ma"] = df["volume"].rolling(window=vol_period).mean()
        df["atr"] = atr(df["high"], df["low"], df["close"], atr_period)
        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 50:
            return Signal(action="hold")

        df = self.prepare(df)
        row = df.iloc[-1]
        prev = df.iloc[-2]

        if pd.isna(row["donchian_high"]) or pd.isna(row["atr"]):
            return Signal(action="hold")

        vol_mult = float(self.params.get("volume_mult", 1.5))
        atr_mult_sl = float(self.params.get("stop_loss_atr_mult", 2.0))
        atr_mult_tp = float(self.params.get("take_profit_atr_mult", 3.0))
        min_atr_pct = float(self.params.get("min_atr_pct", 0.3))

        atr_pct = (row["atr"] / row["close"]) * 100 if row["close"] else 0
        if atr_pct < min_atr_pct:
            return Signal(action="hold")

        volume_ok = row["volume"] > (row["volume_ma"] * vol_mult) if pd.notna(row["volume_ma"]) else False

        if (
            prev["close"] <= prev["donchian_high"]
            and row["close"] > row["donchian_high"]
            and volume_ok
        ):
            sl = row["close"] - atr_mult_sl * row["atr"]
            tp = row["close"] + atr_mult_tp * row["atr"]
            return Signal(
                action="buy",
                strength=0.8,
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={
                    "donchian_high": float(row["donchian_high"]),
                    "volume_ratio": float(row["volume"] / row["volume_ma"]) if row["volume_ma"] else 0,
                },
            )

        if (
            prev["close"] >= prev["donchian_low"]
            and row["close"] < row["donchian_low"]
            and volume_ok
        ):
            sl = row["close"] + atr_mult_sl * row["atr"]
            tp = row["close"] - atr_mult_tp * row["atr"]
            return Signal(
                action="sell",
                strength=0.8,
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={
                    "donchian_low": float(row["donchian_low"]),
                    "volume_ratio": float(row["volume"] / row["volume_ma"]) if row["volume_ma"] else 0,
                },
            )

        return Signal(action="hold")
