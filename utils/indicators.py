"""Lightweight indicator implementations (no external ta library needed)."""

from __future__ import annotations

import pandas as pd
import numpy as np


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = atr(high, low, close, window=1)  # true range series
    atr_n = tr.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()

    plus_di = 100 * (plus_dm.ewm(alpha=1 / window, min_periods=window, adjust=False).mean() / atr_n)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / window, min_periods=window, adjust=False).mean() / atr_n)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()


def bollinger_bands(close: pd.Series, window: int = 20, num_std: float = 2.0):
    mid = close.rolling(window=window).mean()
    std = close.rolling(window=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower
