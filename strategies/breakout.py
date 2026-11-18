"""Breakout strategy: Volatility Expansion + Volume (professional style)."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.indicators import atr, ema


class BreakoutStrategy(BaseStrategy):
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        # Parameters
        channel_period = int(self.params.get("channel_period", 20))
        vol_period = int(self.params.get("volume_ma_period", 20))
        atr_period = int(self.params.get("atr_period", 14))
        atr_ma_period = int(self.params.get("atr_ma_period", 50))
        ema_period = int(self.params.get("ema_period", 50))

        df = df.copy()

        # Channel (similar to Donchian)
        df["channel_high"] = df["high"].rolling(window=channel_period).max()
        df["channel_low"] = df["low"].rolling(window=channel_period).min()

        # Volume
        df["volume_ma"] = df["volume"].rolling(window=vol_period).mean()

        # Volatility
        df["atr"] = atr(df["high"], df["low"], df["close"], atr_period)
        df["atr_ma"] = df["atr"].rolling(window=atr_ma_period).mean()

        # Trend filter
        df["ema"] = ema(df["close"], ema_period)

        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 70:
            return Signal(action="hold")

        df = self.prepare(df)
        row = df.iloc[-1]
        prev = df.iloc[-2]

        required = ["channel_high", "channel_low", "atr", "atr_ma", "volume_ma", "ema"]
        if any(pd.isna(row[c]) for c in required):
            return Signal(action="hold")

        # Tunable thresholds
        vol_mult = float(self.params.get("volume_mult", 1.6))
        atr_expansion = float(self.params.get("atr_expansion", 1.25))  # ATR must be expanding
        atr_mult_sl = float(self.params.get("stop_loss_atr_mult", 1.8))
        atr_mult_tp = float(self.params.get("take_profit_atr_mult", 3.2))
        min_atr_pct = float(self.params.get("min_atr_pct", 0.35))

        # Filters
        atr_pct = (row["atr"] / row["close"]) * 100
        if atr_pct < min_atr_pct:
            return Signal(action="hold")

        volume_ok = row["volume"] > (row["volume_ma"] * vol_mult)
        volatility_expanding = row["atr"] > (row["atr_ma"] * atr_expansion)

        # ===== LONG BREAKOUT =====
        long_break = (
            prev["close"] <= prev["channel_high"]
            and row["close"] > row["channel_high"]
            and row["close"] > row["ema"]
            and volume_ok
            and volatility_expanding
        )

        if long_break:
            sl = row["close"] - atr_mult_sl * row["atr"]
            tp = row["close"] + atr_mult_tp * row["atr"]

            return Signal(
                action="buy",
                strength=0.85,
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={
                    "type": "volatility_breakout_long",
                    "atr_pct": float(atr_pct),
                    "volume_ratio": float(row["volume"] / row["volume_ma"]),
                },
            )

        # ===== SHORT BREAKOUT =====
        short_break = (
            prev["close"] >= prev["channel_low"]
            and row["close"] < row["channel_low"]
            and row["close"] < row["ema"]
            and volume_ok
            and volatility_expanding
        )

        if short_break:
            sl = row["close"] + atr_mult_sl * row["atr"]
            tp = row["close"] - atr_mult_tp * row["atr"]

            return Signal(
                action="sell",
                strength=0.85,
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={
                    "type": "volatility_breakout_short",
                    "atr_pct": float(atr_pct),
                    "volume_ratio": float(row["volume"] / row["volume_ma"]),
                },
            )

        return Signal(action="hold")
