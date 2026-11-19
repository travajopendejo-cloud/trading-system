"""Paper sniper decisions. Every buy, including copies, goes through the filters."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from meme_sniper.core.executor import Executor
from meme_sniper.core.filters import TokenFilter, TokenInfo
from meme_sniper.core.risk import ClosedTrade, Position, SniperRiskManager
from meme_sniper.core.wallet_copy import CopySignal, WalletCopyManager

logger = logging.getLogger(__name__)


def assert_paper_only(sniper: "MemeSniper") -> None:
    if sniper.executor.mode != "paper":
        raise RuntimeError("Meme sniper refused to start because it was not in paper mode")


class MemeSniper:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        requested = str(config.get("mode", "paper")).lower()
        if requested != "paper":
            logger.warning("Config asked for %s mode. Forcing paper mode.", requested)
        capital_cfg = config.get("capital", {}) or {}
        risk_cfg = config.get("risk", {}) or {}

        self.risk = SniperRiskManager(
            initial_capital=float(capital_cfg.get("initial", 100)),
            max_position_usd=float(capital_cfg.get("max_position_usd", 10)),
            max_open_positions=int(capital_cfg.get("max_open_positions", 3)),
            daily_loss_limit=float(capital_cfg.get("daily_loss_limit_usd", 20)),
            take_profit_1=float(risk_cfg.get("take_profit_1", 1.0)),
            take_profit_2=float(risk_cfg.get("take_profit_2", 3.0)),
            take_profit_3=float(risk_cfg.get("take_profit_3", 7.0)),
            stop_loss=float(risk_cfg.get("stop_loss", 0.25)),
        )
        self.filters = TokenFilter(config)
        self.executor = Executor(mode="paper")
        execution = config.get("execution") or {}
        self.executor.cost_rate = float(execution.get("fee_rate", 0.0)) + float(execution.get("slippage_rate", 0.0))
        self.executor.delay_seconds = float(execution.get("delay_seconds", 0.0))
        self.wallet_copy = WalletCopyManager(risk_manager=self.risk)
        self.time_stop_minutes = float(risk_cfg.get("time_stop_minutes", 0))
        fraction = float(risk_cfg.get("take_profit_size", risk_cfg.get("take_profit_1_size", 1 / 3)))
        self.tp1_fraction = min(0.9, max(0.1, fraction))

    def evaluate_token(self, token: TokenInfo) -> tuple[bool, str]:
        can_open, reason = self.risk.can_open()
        if not can_open:
            return False, reason
        return self.filters.is_valid(token)

    def open_trade(self, token: TokenInfo, source: str = "filter") -> Position | None:
        key = self._key(token)
        if key in self.risk.positions:
            logger.info("Skipped %s (%s): already in a paper position", token.symbol, source)
            return None
        ok, reason = self.evaluate_token(token)
        if not ok:
            logger.info("Skipped %s (%s): %s", token.symbol, source, reason)
            return None
        return self._buy(token, key, self.risk.position_size(), source)

    def handle_copy_signal(self, signal: CopySignal, token: TokenInfo) -> Position | None:
        """Paper-copy a wallet buy only after the same safety filters as a normal snipe."""
        if signal.wallet_address not in self.wallet_copy.wallets:
            logger.info("Ignored copy from %s because that wallet is not enabled", signal.wallet_name)
            return None
        if not token.symbol:
            token.symbol = signal.token_symbol
        if not token.address:
            token.address = signal.token_address
        if token.price <= 0:
            token.price = signal.entry_price
        key = self._key(token)
        if key in self.risk.positions:
            logger.info("Skipped copy %s: already in a paper position", token.symbol)
            return None
        ok, reason = self.evaluate_token(token)
        if not ok:
            logger.info("Skipped copy %s from %s: %s", token.symbol, signal.wallet_name, reason)
            return None
        usd_size = min(float(signal.amount_usd), self.risk.position_size())
        return self._buy(token, key, usd_size, f"copy:{signal.wallet_name}")

    def check_position(
        self,
        key: str,
        current_price: float,
        now: datetime | None = None,
    ) -> str | None:
        pos = self.risk.positions.get(key)
        if not pos:
            return None
        moment = now or datetime.now(timezone.utc)
        age_minutes = (moment - pos.entry_time).total_seconds() / 60.0
        if self.time_stop_minutes > 0 and age_minutes >= self.time_stop_minutes:
            self._exit_all(key, current_price, "time", moment)
            return "time"
        signals = self.risk.check_exits(key, current_price)
        if "sl" in signals:
            self._exit_all(key, current_price, "sl", moment)
            return "sl"
        last = None
        original = pos.original_amount or pos.amount
        chunk = original * self.tp1_fraction
        for name in ("tp1", "tp2", "tp3"):
            if name not in signals:
                continue
            current = self.risk.positions.get(key)
            if current is None:
                break
            sell_amount = current.amount if name == "tp3" else min(chunk, current.amount)
            order = self.executor.sell(current.symbol, sell_amount, current_price)
            if order["status"] != "filled":
                return last
            setattr(current, f"{name}_done", True)
            self.risk.reduce_position(key, sell_amount, order["price"], name, now=moment)
            logger.info("%s hit on %s – sold one third", name.upper(), current.symbol)
            last = name
        return last

    def dashboard_positions(self) -> list[dict]:
        rows = []
        for pos in list(self.risk.positions.values()):
            rows.append(
                {
                    "bot": "meme_sniper",
                    "symbol": pos.symbol,
                    "side": "long",
                    "amount": pos.amount,
                    "entry_price": pos.entry_price,
                }
            )
        return rows

    def dashboard_trades(self, limit: int = 15) -> list[dict]:
        return [trade.as_dashboard() for trade in self.risk.get_closed_trades(limit)]

    def _buy(self, token: TokenInfo, key: str, usd_size: float, source: str) -> Position | None:
        if usd_size < 1:
            logger.info("Skipped %s (%s): paper size below $1", token.symbol, source)
            return None
        order = self.executor.buy(token.symbol, usd_size, token.price)
        if order["status"] != "filled":
            logger.warning("Paper buy rejected for %s: %s", token.symbol, order.get("reason"))
            return None
        pos = self.risk.open_position(
            key=key,
            symbol=token.symbol,
            amount=order["amount"],
            entry_price=order["price"],
            token_address=token.address,
            source=source,
        )
        if pos is None:
            logger.warning("Paper buy for %s was not recorded", token.symbol)
            return None
        logger.info("Opened paper position via %s: %s | size $%.2f", source, token.symbol, usd_size)
        return pos

    def _exit_all(self, key: str, price: float, reason: str, now: datetime) -> ClosedTrade | None:
        pos = self.risk.positions.get(key)
        if not pos:
            return None
        order = self.executor.sell(pos.symbol, pos.amount, price)
        if order["status"] != "filled":
            return None
        trade = self.risk.reduce_position(key, pos.amount, price, reason, now=now)
        if trade is not None:
            logger.info("%s on %s | PnL: $%.2f", reason.upper(), pos.symbol, trade.pnl)
        return trade

    @staticmethod
    def _key(token: TokenInfo) -> str:
        if token.address and len(token.address) > 20:
            return token.address
        return token.symbol
