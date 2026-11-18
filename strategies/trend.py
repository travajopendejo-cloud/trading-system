"""Trend-following strategy: Range Breakout + Momentum (professional style)."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.indicators import ema, adx, atr


class TrendStrategy(BaseStrategy):
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        # Parameters (you can later tune these in the bot YAML)
        range_lookback = int(self.params.get("range_lookback", 12))   # bars that form the "range"
        ema_period = int(self.params.get("ema_period", 50))
        adx_period = int(self.params.get("adx_period", 14))
        atr_period = int(self.params.get("atr_period", 14))
        volume_ma_period = int(self.params.get("volume_ma_period", 20))

        df = df.copy()

        # Trend filter
        df["ema"] = ema(df["close"], ema_period)

        # Volatility & strength
        df["adx"] = adx(df["high"], df["low"], df["close"], adx_period)
        df["atr"] = atr(df["high"], df["low"], df["close"], atr_period)

        # Volume confirmation
        df["volume_ma"] = df["volume"].rolling(volume_ma_period).mean()

        # Recent range (acts as "opening range" / noise area)
        df["range_high"] = df["high"].rolling(range_lookback).max()
        df["range_low"] = df["low"].rolling(range_lookback).min()

        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 60:
            return Signal(action="hold")

        df = self.prepare(df)
        row = df.iloc[-1]
        prev = df.iloc[-2]

        # Safety checks
        required = ["ema", "adx", "atr", "range_high", "range_low", "volume_ma"]
        if any(pd.isna(row[c]) for c in required):
            return Signal(action="hold")

        # Tunable thresholds
        adx_threshold = float(self.params.get("adx_threshold", 22.0))
        volume_mult = float(self.params.get("volume_mult", 1.3))
        atr_mult_sl = float(self.params.get("stop_loss_atr_mult", 1.4))
        atr_mult_tp = float(self.params.get("take_profit_atr_mult", 2.8))

        # Volume must be elevated
        volume_ok = row["volume"] > (row["volume_ma"] * volume_mult)

        # ===== LONG: Break above recent range + trend filter =====
        long_break = (
            prev["close"] <= prev["range_high"]
            and row["close"] > row["range_high"]
            and row["close"] > row["ema"]          # above trend
            and row["adx"] >= adx_threshold
            and volume_ok
        )

        if long_break:
            sl = row["close"] - atr_mult_sl * row["atr"]
            tp = row["close"] + atr_mult_tp * row["atr"]
            strength = min(row["adx"] / 45.0, 1.0)

            return Signal(
                action="buy",
                strength=strength,
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={
                    "type": "range_breakout_long",
                    "adx": float(row["adx"]),
                    "atr": float(row["atr"]),
                    "range_high": float(row["range_high"]),
                },
            )

        # ===== SHORT: Break below recent range + trend filter =====
        short_break = (
            prev["close"] >= prev["range_low"]
            and row["close"] < row["range_low"]
            and row["close"] < row["ema"]           # below trend
            and row["adx"] >= adx_threshold
            and volume_ok
        )

        if short_break:
            sl = row["close"] + atr_mult_sl * row["atr"]
            tp = row["close"] - atr_mult_tp * row["atr"]
            strength = min(row["adx"] / 45.0, 1.0)

            return Signal(
                action="sell",
                strength=strength,
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={
                    "type": "range_breakout_short",
                    "adx": float(row["adx"]),
                    "atr": float(row["atr"]),
                    "range_low": float(row["range_low"]),
                },
            )

        return Signal(action="hold")
