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
from bots.ensemble_trend_bot import EnsembleTrendBot
from bots.swing_bot import SwingBot
from core.allocation import portfolio_allocations, scale_sniper_config
from core.performance import calendar_pnl, summarize
from core.portfolio_guard import PortfolioGuard

logger = structlog.get_logger(__name__)


class _MemeSniperAdapter:
    """Lets the dashboard start and stop the paper meme sniper like the other bots."""

    def __init__(self, service: Any) -> None:
        self.service = service
        self.name = "meme_sniper"
        self.running = False
        self.config = type("Cfg", (), {"symbols": ["paper SOL memes"]})()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            self.running = True
            return
        self.running = True
        self.service.running = True
        self._task = asyncio.create_task(self.service.run(), name="meme_sniper")

    async def stop(self) -> None:
        self.running = False
        self.service.running = False
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


BOT_REGISTRY = {
    "trend_bot": TrendBot,
    "mean_reversion_bot": MeanReversionBot,
    "breakout_bot": BreakoutBot,
    "ensemble_trend": EnsembleTrendBot,
    "swing": SwingBot,
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
        self.meme_sniper = None
        self.guard = PortfolioGuard()

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
            allocated = self._allocation_map().get(name, total_equity * allocation_pct)

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
            if hasattr(bot, "attach_guard"):
                bot.attach_guard(self.guard)
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
            get_performance=self.get_performance,
        )
        self.dashboard.start()

        try:
            await self._start_meme_sniper()
        except Exception as exc:
            logger.exception("meme_sniper_not_started", error=str(exc))

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
                "note": getattr(bot, "status_note", "") or "",
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
        for name, bot in self.bots.items():
            for symbol, weight in (getattr(bot, "weights", {}) or {}).items():
                if weight <= 0:
                    continue
                positions.append({
                    "bot": name,
                    "symbol": symbol,
                    "side": "long",
                    "amount": round(weight, 4),
                    "entry_price": (getattr(bot, "last_prices", {}) or {}).get(symbol),
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

        service = getattr(self, "meme_sniper", None)
        if service is not None:
            try:
                positions.extend(service.dashboard_positions())
                closed_trades.extend(service.dashboard_trades(limit=15))
            except Exception as exc:
                logger.warning("meme_sniper_status_failed", error=str(exc))
        closed_trades.sort(key=lambda row: row.get("closed_at") or "", reverse=True)
        closed_trades = closed_trades[:15]

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
            "closed_trades": closed_trades,
            "halted": self.risk.halted if self.risk else False,
            "sniper": self._sniper_status(),
            "allocation": self._allocation_for_status(),
            "risk_guard": self.guard.status(),
            "calendar": self._calendar(),
        }

    def _sniper_settings(self) -> tuple[bool, float]:
        from meme_sniper.core.settings import meme_sniper_enabled

        section = self.cfg.meme_sniper or {}
        enabled = True
        share = 0.20
        if isinstance(section, dict):
            enabled = bool(section.get("enabled", True))
            share = section.get("portfolio_share", 0.20)
        return meme_sniper_enabled(enabled), share

    def allocation_rows(self) -> list[dict]:
        enabled, share = self._sniper_settings()
        return portfolio_allocations(
            self.initial_equity,
            self.cfg.capital_allocation,
            sniper_share=share,
            sniper_enabled=enabled,
        )

    def _allocation_map(self) -> dict[str, float]:
        return {row["key"]: float(row["usd"]) for row in self.allocation_rows()}

    def _allocation_for_status(self) -> list[dict]:
        return [
            {
                "key": row["key"],
                "name": row["name"],
                "pct": round(float(row["pct"]), 1),
                "usd": round(float(row["usd"]), 2),
            }
            for row in self.allocation_rows()
        ]

    def get_performance(self) -> dict:
        """Closed-trade results for the performance page."""
        rows = self.allocation_rows()
        grouped: dict[str, list[dict]] = {row["key"]: [] for row in rows}
        combined: list[dict] = []

        def add(bot: str, symbol: str, pnl: float, closed_at) -> None:
            if hasattr(closed_at, "isoformat"):
                closed_at = closed_at.isoformat()
            item = {
                "bot": bot,
                "symbol": symbol,
                "pnl": float(pnl),
                "closed_at": closed_at or "",
            }
            combined.append(item)
            grouped.setdefault(bot, []).append(item)

        if self.risk:
            for trade in list(self.risk.closed_trades):
                add(trade.bot_name, trade.symbol, trade.pnl, trade.closed_at)
        service = getattr(self, "meme_sniper", None)
        if service is not None:
            try:
                for trade in list(service.sniper.risk.closed_trades):
                    add("meme_sniper", trade.symbol, trade.pnl, trade.closed_at)
            except Exception as exc:
                logger.warning("meme_sniper_performance_failed", error=str(exc))

        starts = {row["key"]: float(row["usd"]) for row in rows}
        if service is not None:
            try:
                starts["meme_sniper"] = float(service.sniper.risk.initial_capital)
            except Exception:
                pass
        for name, bot in self.bots.items():
            allocated = getattr(bot, "allocated_equity", None)
            if allocated is not None and name in starts:
                starts[name] = float(allocated)

        bots = []
        for row in rows:
            report = summarize(row["name"], starts.get(row["key"], row["usd"]), grouped.get(row["key"], []))
            report["key"] = row["key"]
            report["allocation_pct"] = round(float(row["pct"]), 1)
            bots.append(report)
        portfolio = summarize("Whole portfolio", self.initial_equity, combined)
        sniper_pct = next((row["pct"] for row in rows if row["key"] == "meme_sniper"), 0.0)
        from backtest.harness import load_report

        backtests = load_report() or {}
        return {
            "mode": "paper",
            "portfolio": portfolio,
            "bots": bots,
            "allocation": self._allocation_for_status(),
            "sniper_share_pct": round(float(sniper_pct), 1),
            "note": (
                "Win rate and profit count closed paper trades only. "
                "A trade that is still open is not a win or a loss yet. "
                f"The meme sniper is set to {float(sniper_pct):.1f}% of the account."
            ),
            "backtests": backtests.get("strategies", []),
            "backtest_note": backtests.get("method", ""),
            "leverage_comparison": backtests.get("leverage_comparison"),
            "calendar": calendar_pnl(combined, bots=[(row["key"], row["name"]) for row in rows]),
            "risk_guard": self.guard.status(),
        }

    def _calendar(self) -> dict:
        trades = []
        if self.risk:
            for trade in self.risk.closed_trades:
                closed = trade.closed_at
                trades.append(
                    {
                        "bot": trade.bot_name,
                        "pnl": trade.pnl,
                        "closed_at": closed.isoformat() if hasattr(closed, "isoformat") else closed,
                    }
                )
        service = getattr(self, "meme_sniper", None)
        if service is not None:
            try:
                for trade in service.sniper.risk.closed_trades:
                    closed = trade.closed_at
                    trades.append(
                        {
                            "bot": "meme_sniper",
                            "pnl": trade.pnl,
                            "closed_at": closed.isoformat() if hasattr(closed, "isoformat") else closed,
                        }
                    )
            except Exception as exc:
                logger.warning("meme_sniper_calendar_failed", error=str(exc))
        bots = [(row["key"], row["name"]) for row in self.allocation_rows()]
        return calendar_pnl(trades, bots=bots)

    def _sniper_status(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "mode": "paper",
            "enabled": False,
            "capital": None,
            "daily_pnl": None,
            "tracked_wallets": [],
            "disabled_wallets": [],
            "last_error": None,
            "rpc": None,
            "helius": False,
            "rejections": [],
            "copying": [],
        }
        try:
            from meme_sniper.core.wallet_copy import TRACKED_WALLETS
            summary["tracked_wallets"] = [wallet.name for wallet in TRACKED_WALLETS if wallet.enabled]
            summary["disabled_wallets"] = [wallet.name for wallet in TRACKED_WALLETS if not wallet.enabled]
        except Exception as exc:
            summary["last_error"] = str(exc)
            return summary
        service = getattr(self, "meme_sniper", None)
        if service is not None:
            try:
                summary.update(service.status_summary())
            except Exception as exc:
                summary["last_error"] = str(exc)
        return summary

    async def _start_meme_sniper(self) -> None:
        from meme_sniper.core.service import MemeSniperService
        from meme_sniper.core.settings import load_sniper_config, meme_sniper_enabled

        section = self.cfg.meme_sniper or {}
        enabled = True
        config_file = "meme_sniper/config.yaml"
        if isinstance(section, dict):
            enabled = bool(section.get("enabled", True))
            config_file = section.get("config_file", config_file)
        if not meme_sniper_enabled(enabled):
            logger.info("meme_sniper_disabled")
            return
        config = load_sniper_config(config_file)
        allocated = self._allocation_map().get("meme_sniper", 0.0)
        _enabled, share = self._sniper_settings()
        config = scale_sniper_config(config, allocated, share)
        service = MemeSniperService(config)
        self.meme_sniper = service
        logger.info(
            "meme_sniper_allocation",
            usd=round(allocated, 2),
            max_position=config["capital"]["max_position_usd"],
            daily_loss=config["capital"]["daily_loss_limit_usd"],
            max_positions=config["capital"]["max_open_positions"],
        )
        adapter = _MemeSniperAdapter(service)
        self.bots["meme_sniper"] = adapter
        await adapter.start()
        logger.info("meme_sniper_started", mode="paper")

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
