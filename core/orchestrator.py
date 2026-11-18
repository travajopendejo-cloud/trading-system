"""Central orchestrator / portfolio manager."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import structlog

from core.config import GlobalConfig, load_bot_config, get_settings
from core.exchange import ExchangeClient
from core.mock_exchange import MockExchangeClient
from core.risk import RiskManager
from core.metrics import Metrics
from core.telegram_notifier import TelegramNotifier
from core.dashboard import DashboardServer
from bots.trend_bot import TrendBot
from bots.mean_reversion_bot import MeanReversionBot
from bots.breakout_bot import BreakoutBot

logger = structlog.get_logger(__name__)

BOT_REGISTRY = {
    "trend_bot": TrendBot,
    "mean_reversion_bot": MeanReversionBot,
    "breakout_bot": BreakoutBot,
}


class Orchestrator:
    def __init__(self, global_cfg: GlobalConfig) -> None:
        self.cfg = global_cfg
        self.settings = get_settings()
        self.bots: Dict[str, Any] = {}
        self.exchange: ExchangeClient | MockExchangeClient | None = None
        self.risk: RiskManager | None = None
        self.metrics = Metrics()
        self.telegram: TelegramNotifier | None = None
        self.dashboard: DashboardServer | None = None
        self._monitor_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.initial_equity = float(global_cfg.trading.get("initial_equity", 10_000))

    async def start(self) -> None:
        logger.info("orchestrator_starting")
        self._loop = asyncio.get_running_loop()

        # Exchange – use mock when mode is "mock" or "demo"
        mode = (self.settings.trading_mode or "paper").lower()
        if mode in ("mock", "demo"):
            logger.info("using_mock_exchange")
            self.exchange = MockExchangeClient(seed=42)
        else:
            self.exchange = ExchangeClient(
                exchange_id=self.cfg.exchange.get("id", "binance"),
                api_key=self.settings.api_key,
                api_secret=self.settings.api_secret,
                password=self.settings.passphrase,
                sandbox=self.cfg.exchange.get("sandbox", True),
                default_type=self.cfg.exchange.get("options", {}).get("defaultType", "future"),
            )
        await self.exchange.connect()

        # Risk
        self.risk = RiskManager(
            initial_equity=self.initial_equity,
            max_drawdown_pct=self.cfg.risk.max_drawdown_pct,
            daily_loss_limit_pct=self.cfg.risk.daily_loss_limit_pct,
            max_total_exposure_pct=self.cfg.risk.max_total_exposure_pct,
            max_open_positions=self.cfg.risk.max_open_positions,
        )

        # Telegram
        self.telegram = TelegramNotifier(
            token=self.settings.telegram_bot_token,
            chat_id=self.settings.telegram_chat_id,
            enabled=self.settings.telegram_enabled and self.cfg.telegram.enabled,
            rate_limit_per_minute=self.cfg.telegram.rate_limit_per_minute,
        )
        await self.telegram.start()

        # Metrics
        if self.cfg.metrics.enabled:
            port = self.settings.metrics_port or self.cfg.metrics.port
            self.metrics.start_server(port)
            self.metrics.set_system_info(
                {
                    "mode": self.settings.trading_mode,
                    "exchange": self.cfg.exchange.get("id", "binance"),
                }
            )

        # Capital allocation & bot creation
        total_equity = self.initial_equity
        mode = self.settings.trading_mode

        for bot_entry in self.cfg.bots:
            if not bot_entry.get("enabled", True):
                continue
            name = bot_entry["name"]
            cfg_path = bot_entry["config_file"]
            bot_cfg = load_bot_config(cfg_path)

            allocation_pct = self.cfg.capital_allocation.get(name, 0.0)
            allocated = total_equity * allocation_pct

            bot_cls = BOT_REGISTRY.get(name)
            if not bot_cls:
                logger.warning("unknown_bot", name=name)
                continue

            bot = bot_cls.from_config(
                config=bot_cfg,
                exchange=self.exchange,
                risk_manager=self.risk,
                metrics=self.metrics,
                telegram=self.telegram,
                allocated_equity=allocated,
                trading_mode=mode,
            )
            self.bots[name] = bot
            await bot.start()
            logger.info("bot_launched", name=name, allocation=allocated)

        # Web dashboard
        import os
        dash_port = int(os.environ.get("PORT", os.environ.get("DASHBOARD_PORT", "8080")))
        self.dashboard = DashboardServer(
            host="0.0.0.0",
            port=dash_port,
            get_status=self.get_status,
            control_bot=self._sync_control_bot,
            get_metrics=self.metrics.generate,
        )
        self.dashboard.start()

        await self.telegram.notify_status(
            "System Started",
            f"Mode: {mode}\nBots: {', '.join(self.bots.keys())}\nEquity: {total_equity:.2f}\nDashboard: http://localhost:8080",
        )

        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("orchestrator_started", bots=list(self.bots.keys()), dashboard="http://localhost:8080")

    async def stop(self) -> None:
        logger.info("orchestrator_stopping")
        if self._monitor_task:
            self._monitor_task.cancel()
        if self.dashboard:
            self.dashboard.stop()
        for bot in self.bots.values():
            await bot.stop()
        if self.telegram:
            await self.telegram.stop()
        if self.exchange:
            await self.exchange.close()
        logger.info("orchestrator_stopped")

    def get_status(self) -> dict[str, Any]:
        """Return current system status for the dashboard API."""
        bots_info = []
        for name, bot in self.bots.items():
            status = "stopped"
            if getattr(bot, "running", False):
                status = "running"
            symbols = ",".join(getattr(bot.config, "symbols", [])[:3])
            bots_info.append({
                "name": name,
                "status": status,
                "symbols": symbols,
            })

        positions = []
        if self.risk:
            for pos in self.risk.get_positions():
                positions.append({
                    "bot": pos.bot_name,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "amount": pos.amount,
                    "entry_price": pos.entry_price,
                })

        # Closed trades history
        closed_trades = []
        if self.risk:
            for t in self.risk.get_closed_trades(limit=15):
                closed_trades.append({
                    "bot": t.bot_name,
                    "symbol": t.symbol,
                    "side": t.side,
                    "amount": t.amount,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "pnl": t.pnl,
                    "opened_at": t.opened_at.isoformat(),
                    "closed_at": t.closed_at.isoformat(),
                })

        equity = self.risk.equity if self.risk else self.initial_equity
        daily_pnl = self.risk.daily_pnl if self.risk else 0.0
        drawdown = self.risk.current_drawdown_pct if self.risk else 0.0

        return {
            "mode": self.settings.trading_mode,
            "equity": equity,
            "initial_equity": self.initial_equity,
            "daily_pnl": daily_pnl,
            "drawdown_pct": drawdown,
            "open_positions": len(positions),
            "bots": bots_info,
            "positions": positions,
            "closed_trades": closed_trades,          # ← NEW
            "halted": self.risk.halted if self.risk else False,
        }

    def _sync_control_bot(self, bot_name: str, action: str) -> dict[str, Any]:
        """Called from the dashboard HTTP thread – schedule coroutine on the main loop."""
        if not self._loop:
            return {"ok": False, "error": "Event loop not ready"}

        future = asyncio.run_coroutine_threadsafe(
            self.control_bot(bot_name, action),
            self._loop,
        )
        try:
            return future.result(timeout=10)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def control_bot(self, bot_name: str, action: str) -> dict[str, Any]:
        """Start or stop one bot (or all)."""
        action = action.lower()
        if action not in ("start", "stop"):
            return {"ok": False, "error": f"Unknown action: {action}"}

        targets = []
        if bot_name == "all":
            targets = list(self.bots.values())
        elif bot_name in self.bots:
            targets = [self.bots[bot_name]]
        else:
            return {"ok": False, "error": f"Unknown bot: {bot_name}"}

        for bot in targets:
            if action == "start":
                if not bot.running:
                    await bot.start()
                    logger.info("bot_started_via_dashboard", bot=bot.name)
            else:
                if bot.running:
                    await bot.stop()
                    logger.info("bot_stopped_via_dashboard", bot=bot.name)

        return {"ok": True, "action": action, "bot": bot_name}

    async def _monitor_loop(self) -> None:
        while True:
            try:
                if self.risk:
                    self.metrics.set_equity(self.risk.equity, scope="total")
                    self.metrics.set_drawdown(self.risk.current_drawdown_pct, scope="total")
                    self.metrics.set_open_positions(len(self.risk.positions), scope="total")
                    if self.risk.halted:
                        logger.critical("portfolio_halted", reason=self.risk.halt_reason)
                        await self.telegram.notify_status(
                            "PORTFOLIO HALTED",
                            self.risk.halt_reason or "Unknown reason",
                        )
                        for bot in self.bots.values():
                            await bot.stop()
                        break
            except Exception as exc:
                logger.exception("monitor_error", error=str(exc))
            await asyncio.sleep(30)
