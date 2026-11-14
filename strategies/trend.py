"""Trend-following strategy: EMA crossover + ADX filter."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.indicators import ema, adx, atr


class TrendStrategy(BaseStrategy):
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        fast = int(self.params.get("fast_ema", 12))
        slow = int(self.params.get("slow_ema", 26))
        adx_period = int(self.params.get("adx_period", 14))
        atr_period = int(self.params.get("atr_period", 14))

        df = df.copy()
        df["ema_fast"] = ema(df["close"], fast)
        df["ema_slow"] = ema(df["close"], slow)
        df["adx"] = adx(df["high"], df["low"], df["close"], adx_period)
        df["atr"] = atr(df["high"], df["low"], df["close"], atr_period)
        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 50:
            return Signal(action="hold")

        df = self.prepare(df)
        row = df.iloc[-1]
        prev = df.iloc[-2]

        adx_threshold = float(self.params.get("adx_threshold", 25.0))
        atr_mult_sl = float(self.params.get("stop_loss_atr_mult", 1.5))
        atr_mult_tp = float(self.params.get("take_profit_atr_mult", 2.5))

        if pd.isna(row["ema_fast"]) or pd.isna(row["adx"]) or pd.isna(row["atr"]):
            return Signal(action="hold")

        # Bullish crossover
        if (
            prev["ema_fast"] <= prev["ema_slow"]
            and row["ema_fast"] > row["ema_slow"]
            and row["adx"] >= adx_threshold
        ):
            sl = row["close"] - atr_mult_sl * row["atr"]
            tp = row["close"] + atr_mult_tp * row["atr"]
            return Signal(
                action="buy",
                strength=min(row["adx"] / 50, 1.0),
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={"adx": float(row["adx"]), "atr": float(row["atr"])},
            )

        # Bearish crossover
        if (
            prev["ema_fast"] >= prev["ema_slow"]
            and row["ema_fast"] < row["ema_slow"]
            and row["adx"] >= adx_threshold
        ):
            sl = row["close"] + atr_mult_sl * row["atr"]
            tp = row["close"] - atr_mult_tp * row["atr"]
            return Signal(
                action="sell",
                strength=min(row["adx"] / 50, 1.0),
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={"adx": float(row["adx"]), "atr": float(row["atr"])},
            )

        return Signal(action="hold")
