"""Mean-reversion strategy: Session Mean (VWAP-style) + RSI (professional style)."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.indicators import rsi, atr, ema


class MeanReversionStrategy(BaseStrategy):
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        # Parameters
        mean_period = int(self.params.get("mean_period", 20))      # acts like session VWAP / mean
        rsi_period = int(self.params.get("rsi_period", 14))
        atr_period = int(self.params.get("atr_period", 14))
        std_period = int(self.params.get("std_period", 20))

        df = df.copy()

        # Session-style mean (simple but effective approximation of VWAP for crypto)
        df["mean"] = ema(df["close"], mean_period)

        # Distance from the mean (z-score style)
        df["std"] = df["close"].rolling(std_period).std()
        df["zscore"] = (df["close"] - df["mean"]) / df["std"]

        df["rsi"] = rsi(df["close"], rsi_period)
        df["atr"] = atr(df["high"], df["low"], df["close"], atr_period)

        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 50:
            return Signal(action="hold")

        df = self.prepare(df)
        row = df.iloc[-1]

        required = ["mean", "zscore", "rsi", "atr"]
        if any(pd.isna(row[c]) for c in required):
            return Signal(action="hold")

        # Tunable thresholds
        zscore_entry = float(self.params.get("zscore_entry", 1.8))
        rsi_oversold = float(self.params.get("rsi_oversold", 32))
        rsi_overbought = float(self.params.get("rsi_overbought", 68))
        atr_mult_sl = float(self.params.get("stop_loss_atr_mult", 1.3))
        atr_mult_tp = float(self.params.get("take_profit_atr_mult", 1.6))

        # ===== LONG: Price stretched below mean + RSI oversold =====
        if row["zscore"] < -zscore_entry and row["rsi"] < rsi_oversold:
            sl = row["close"] - atr_mult_sl * row["atr"]
            tp = row["mean"]   # target the mean (classic mean reversion)
            strength = min(abs(row["zscore"]) / 3.0, 1.0)

            return Signal(
                action="buy",
                strength=strength,
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={
                    "type": "mean_reversion_long",
                    "zscore": float(row["zscore"]),
                    "rsi": float(row["rsi"]),
                },
            )

        # ===== SHORT: Price stretched above mean + RSI overbought =====
        if row["zscore"] > zscore_entry and row["rsi"] > rsi_overbought:
            sl = row["close"] + atr_mult_sl * row["atr"]
            tp = row["mean"]
            strength = min(abs(row["zscore"]) / 3.0, 1.0)

            return Signal(
                action="sell",
                strength=strength,
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={
                    "type": "mean_reversion_short",
                    "zscore": float(row["zscore"]),
                    "rsi": float(row["rsi"]),
                },
            )

        return Signal(action="hold")
