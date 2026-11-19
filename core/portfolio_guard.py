"""Drawdown brakes for the paper portfolio.

A bot pauses after a 15% drop from its own peak. The whole portfolio
cuts size in half after a 10% drop and goes to cash after a 20% drop.
"""

from __future__ import annotations


class PortfolioGuard:
    def __init__(self) -> None:
        self.equity = 0.0
        self.peak = 0.0
        self.bot_equity: dict[str, float] = {}
        self.bot_peak: dict[str, float] = {}
        self.paused_bots: set[str] = set()

    def update_portfolio(self, equity: float) -> None:
        self.equity = float(equity)
        self.peak = max(self.peak, self.equity)

    def update_bot(self, name: str, equity: float) -> None:
        value = float(equity)
        self.bot_equity[name] = value
        self.bot_peak[name] = max(self.bot_peak.get(name, value), value)
        drop = self.bot_drawdown(name)
        if drop is not None and drop <= -15:
            self.paused_bots.add(name)
        elif name in self.paused_bots and drop is not None and drop > -15:
            # Stay paused. A person has to turn the bot back on.
            pass

    def portfolio_drawdown(self) -> float | None:
        if self.peak <= 0:
            return None
        return (self.equity - self.peak) / self.peak * 100.0

    def bot_drawdown(self, name: str) -> float | None:
        peak = self.bot_peak.get(name, 0.0)
        equity = self.bot_equity.get(name)
        if equity is None or peak <= 0:
            return None
        return (equity - peak) / peak * 100.0

    @property
    def state(self) -> str:
        drop = self.portfolio_drawdown()
        if drop is None:
            return "normal"
        if drop <= -20:
            return "cash"
        if drop <= -10:
            return "halved"
        return "normal"

    def exposure_scale(self, bot_name: str | None = None) -> float:
        if bot_name and bot_name in self.paused_bots:
            return 0.0
        if self.state == "cash":
            return 0.0
        if self.state == "halved":
            return 0.5
        return 1.0

    def resume_bot(self, name: str) -> None:
        self.paused_bots.discard(name)

    def status(self) -> dict:
        drop = self.portfolio_drawdown()
        labels = {
            "normal": "Normal. Full paper size.",
            "halved": "Portfolio is down 10% or more. New size is cut in half.",
            "cash": "Portfolio is down 20% or more. Strategy is in cash.",
        }
        return {
            "state": self.state,
            "label": labels[self.state],
            "drawdown_pct": None if drop is None else round(drop, 2),
            "paused_bots": sorted(self.paused_bots),
            "scale": self.exposure_scale(),
        }
