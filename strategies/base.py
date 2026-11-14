"""Base strategy interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class Signal:
    action: str  # "buy" | "sell" | "close" | "hold"
    strength: float = 1.0
    stop_loss: float | None = None
    take_profit: float | None = None
    meta: dict[str, Any] | None = None


class BaseStrategy(ABC):
    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Generate trading signal from OHLCV dataframe.
        DataFrame columns: timestamp, open, high, low, close, volume
        Index should be datetime or integer.
        """
        ...

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optional indicator pre-calculation. Override if needed."""
        return df
