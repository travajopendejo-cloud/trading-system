"""Prometheus metrics collectors. Endpoint is served by the dashboard on /metrics."""

from __future__ import annotations

from typing import Dict

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        Info,
        CollectorRegistry,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"

import logging
logger = logging.getLogger(__name__)


class Metrics:
    def __init__(self) -> None:
        self.enabled = PROM_AVAILABLE
        if not PROM_AVAILABLE:
            logger.warning("prometheus_client not installed – metrics disabled")
            return

        self.registry = CollectorRegistry()

        self.equity = Gauge(
            "trading_equity",
            "Current equity in USDT",
            ["scope"],
            registry=self.registry,
        )
        self.unrealized_pnl = Gauge(
            "trading_unrealized_pnl",
            "Unrealized PnL in USDT",
            ["scope"],
            registry=self.registry,
        )
        self.realized_pnl = Gauge(
            "trading_realized_pnl",
            "Realized PnL in USDT",
            ["scope"],
            registry=self.registry,
        )
        self.open_positions = Gauge(
            "trading_open_positions",
            "Number of open positions",
            ["scope"],
            registry=self.registry,
        )
        self.trades_total = Counter(
            "trading_trades_total",
            "Total number of closed trades",
            ["bot", "result"],
            registry=self.registry,
        )
        self.drawdown = Gauge(
            "trading_drawdown_pct",
            "Current drawdown percentage",
            ["scope"],
            registry=self.registry,
        )
        self.order_latency = Histogram(
            "trading_order_latency_seconds",
            "Order execution latency",
            ["bot"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
            registry=self.registry,
        )
        self.errors_total = Counter(
            "trading_errors_total",
            "Total errors",
            ["bot", "type"],
            registry=self.registry,
        )
        self.bot_status = Gauge(
            "trading_bot_status",
            "Bot status (1=running, 0=stopped, -1=error)",
            ["bot"],
            registry=self.registry,
        )
        self.info = Info(
            "trading_system",
            "Trading system information",
            registry=self.registry,
        )

    def start_server(self, port: int = 8000) -> None:
        """Kept for compatibility – metrics are now also served on the dashboard /metrics."""
        if not self.enabled:
            return
        try:
            from prometheus_client import start_http_server
            start_http_server(port, registry=self.registry)
            logger.info("prometheus_metrics_started on port %s", port)
        except Exception as exc:
            logger.warning("could_not_start_metrics_server: %s (metrics still available at dashboard /metrics)", exc)

    def generate(self) -> bytes:
        if not self.enabled:
            return b"# prometheus_client not installed\n"
        return generate_latest(self.registry)

    def set_equity(self, value: float, scope: str = "total") -> None:
        if self.enabled:
            self.equity.labels(scope=scope).set(value)

    def set_unrealized_pnl(self, value: float, scope: str = "total") -> None:
        if self.enabled:
            self.unrealized_pnl.labels(scope=scope).set(value)

    def set_realized_pnl(self, value: float, scope: str = "total") -> None:
        if self.enabled:
            self.realized_pnl.labels(scope=scope).set(value)

    def set_open_positions(self, value: int, scope: str = "total") -> None:
        if self.enabled:
            self.open_positions.labels(scope=scope).set(value)

    def set_drawdown(self, value: float, scope: str = "total") -> None:
        if self.enabled:
            self.drawdown.labels(scope=scope).set(value)

    def record_trade(self, bot: str, win: bool) -> None:
        if self.enabled:
            self.trades_total.labels(bot=bot, result="win" if win else "loss").inc()

    def record_order_latency(self, bot: str, seconds: float) -> None:
        if self.enabled:
            self.order_latency.labels(bot=bot).observe(seconds)

    def record_error(self, bot: str, error_type: str = "general") -> None:
        if self.enabled:
            self.errors_total.labels(bot=bot, type=error_type).inc()

    def set_bot_status(self, bot: str, status: int) -> None:
        if self.enabled:
            self.bot_status.labels(bot=bot).set(status)

    def set_system_info(self, info: Dict[str, str]) -> None:
        if self.enabled:
            self.info.info(info)
