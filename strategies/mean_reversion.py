"""Mean-reversion bot: Bollinger band stretch plus RSI, skipped in strong trends."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.indicators import adx, atr, bollinger_bands, rsi


class MeanReversionStrategy(BaseStrategy):
    """Buy a stretched dip and sell a stretched rally, aiming back at the middle.

    bb_period, bb_std, and the RSI levels come from the bot config. ADX above
    the trend threshold means a real trend is underway, so the bot stands aside
    instead of fading it.
    """

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        bb_period = int(self.params.get("bb_period", self.params.get("mean_period", 20)))
        bb_std = float(self.params.get("bb_std", 2.0))
        rsi_period = int(self.params.get("rsi_period", 14))
        atr_period = int(self.params.get("atr_period", 14))
        adx_period = int(self.params.get("adx_period", 14))
        out = df.copy()
        upper, mid, lower = bollinger_bands(out["close"], bb_period, bb_std)
        out["bb_upper"] = upper
        out["bb_mid"] = mid
        out["bb_lower"] = lower
        out["rsi"] = rsi(out["close"], rsi_period)
        out["atr"] = atr(out["high"], out["low"], out["close"], atr_period)
        out["adx"] = adx(out["high"], out["low"], out["close"], adx_period)
        return out

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 60:
            return Signal(action="hold")

        prepared = self.prepare(df)
        row = prepared.iloc[-1]
        required = ["bb_upper", "bb_mid", "bb_lower", "rsi", "atr"]
        if any(pd.isna(row[col]) for col in required):
            return Signal(action="hold")
        if row["atr"] <= 0 or row["close"] <= 0:
            return Signal(action="hold")

        # Same 25 ADX line the trend bot uses. Above it, this is a trend, not a fade.
        # A missing ADX does not block the trade; we only skip when the trend is known.
        adx_max = float(self.params.get("adx_max", 25.0))
        if not pd.isna(row["adx"]) and row["adx"] >= adx_max:
            return Signal(action="hold")

        rsi_oversold = float(self.params.get("rsi_oversold", 30))
        rsi_overbought = float(self.params.get("rsi_overbought", 70))
        stop_mult = float(self.params.get("stop_loss_atr_mult", 1.2))
        target = float(row["bb_mid"])
        # Skip a target so close that fees would eat it.
        if abs(target - row["close"]) < 0.5 * row["atr"]:
            return Signal(action="hold")

        if row["close"] < row["bb_lower"] and row["rsi"] < rsi_oversold and target > row["close"]:
            stretch = (row["bb_lower"] - row["close"]) / row["atr"]
            return Signal(
                action="buy",
                strength=min(float(stretch) / 2.0, 1.0),
                stop_loss=float(row["close"] - stop_mult * row["atr"]),
                take_profit=target,
                meta={"type": "band_reversion_long", "rsi": float(row["rsi"]), "adx": float(row["adx"])},
            )
        if row["close"] > row["bb_upper"] and row["rsi"] > rsi_overbought and target < row["close"]:
            stretch = (row["close"] - row["bb_upper"]) / row["atr"]
            return Signal(
                action="sell",
                strength=min(float(stretch) / 2.0, 1.0),
                stop_loss=float(row["close"] + stop_mult * row["atr"]),
                take_profit=target,
                meta={"type": "band_reversion_short", "rsi": float(row["rsi"]), "adx": float(row["adx"])},
            )
        return Signal(action="hold")
