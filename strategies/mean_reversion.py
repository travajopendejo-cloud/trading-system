"""Mean-reversion strategy: Bollinger Bands + RSI."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.indicators import rsi, atr, bollinger_bands


class MeanReversionStrategy(BaseStrategy):
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        bb_period = int(self.params.get("bb_period", 20))
        bb_std = float(self.params.get("bb_std", 2.0))
        rsi_period = int(self.params.get("rsi_period", 14))
        atr_period = int(self.params.get("atr_period", 14))

        df = df.copy()
        upper, mid, lower = bollinger_bands(df["close"], bb_period, bb_std)
        df["bb_high"] = upper
        df["bb_mid"] = mid
        df["bb_low"] = lower
        df["rsi"] = rsi(df["close"], rsi_period)
        df["atr"] = atr(df["high"], df["low"], df["close"], atr_period)
        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 50:
            return Signal(action="hold")

        df = self.prepare(df)
        row = df.iloc[-1]

        if pd.isna(row["rsi"]) or pd.isna(row["bb_low"]) or pd.isna(row["atr"]):
            return Signal(action="hold")

        rsi_oversold = float(self.params.get("rsi_oversold", 30))
        rsi_overbought = float(self.params.get("rsi_overbought", 70))
        atr_mult_sl = float(self.params.get("stop_loss_atr_mult", 1.2))
        atr_mult_tp = float(self.params.get("take_profit_atr_mult", 1.8))

        if row["close"] < row["bb_low"] and row["rsi"] < rsi_oversold:
            sl = row["close"] - atr_mult_sl * row["atr"]
            tp = row["bb_mid"]
            return Signal(
                action="buy",
                strength=max(0.3, (rsi_oversold - row["rsi"]) / rsi_oversold),
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={"rsi": float(row["rsi"]), "bb_low": float(row["bb_low"])},
            )

        if row["close"] > row["bb_high"] and row["rsi"] > rsi_overbought:
            sl = row["close"] + atr_mult_sl * row["atr"]
            tp = row["bb_mid"]
            return Signal(
                action="sell",
                strength=max(0.3, (row["rsi"] - rsi_overbought) / (100 - rsi_overbought)),
                stop_loss=float(sl),
                take_profit=float(tp),
                meta={"rsi": float(row["rsi"]), "bb_high": float(row["bb_high"])},
            )

        return Signal(action="hold")
