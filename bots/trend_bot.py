"""Trend-following bot."""

from __future__ import annotations

from bots.base_bot import BaseBot
from strategies.trend import TrendStrategy


class TrendBot(BaseBot):
    """EMA crossover + ADX trend bot."""

    @classmethod
    def from_config(cls, config, exchange, risk_manager, metrics, telegram, allocated_equity, trading_mode="paper"):
        strategy = TrendStrategy(params={**config.parameters, **config.risk.model_dump()})
        return cls(
            config=config,
            strategy=strategy,
            exchange=exchange,
            risk_manager=risk_manager,
            metrics=metrics,
            telegram=telegram,
            allocated_equity=allocated_equity,
            trading_mode=trading_mode,
        )
