"""Main sniper logic with support for Wallet Copy signals."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .risk import SniperRiskManager, Position
from .filters import TokenFilter, TokenInfo
from .executor import Executor
from .wallet_copy import WalletCopyManager, CopySignal

logger = logging.getLogger(__name__)


class MemeSniper:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        capital_cfg = config.get("capital", {})
        risk_cfg = config.get("risk", {})

        self.risk = SniperRiskManager(
            initial_capital=float(capital_cfg.get("initial", 100)),
            max_position_usd=float(capital_cfg.get("max_position_usd", 10)),
            max_open_positions=int(capital_cfg.get("max_open_positions", 3)),
            daily_loss_limit=float(capital_cfg.get("daily_loss_limit_usd", 20)),
            take_profit_1=float(risk_cfg.get("take_profit_1", 0.80)),
            take_profit_2=float(risk_cfg.get("take_profit_2", 1.60)),
            stop_loss=float(risk_cfg.get("stop_loss", 0.35)),
        )

        self.filters = TokenFilter(config)
        self.executor = Executor(mode=config.get("mode", "paper"))
        self.wallet_copy = WalletCopyManager(risk_manager=self.risk)
        self.time_stop_minutes = float(risk_cfg.get("time_stop_minutes", 12))

    def evaluate_token(self, token: TokenInfo) -> tuple[bool, str]:
        can_open, reason = self.risk.can_open()
        if not can_open:
            return False, reason
        return self.filters.is_valid(token)

    def open_trade(self, token: TokenInfo, source: str = "filter") -> Optional[Position]:
        """Try to open a new position."""
        ok, reason = self.evaluate_token(token)
        if not ok:
            logger.info(f"Skipped {token.symbol} ({source}): {reason}")
            return None

        usd_size = self.risk.position_size()
        order = self.executor.buy(token.symbol, usd_size, token.price)

        if order["status"] != "filled":
            logger.warning(f"Buy failed for {token.symbol}")
            return None

        pos = self.risk.open_position(
            symbol=token.symbol,
            amount=order["amount"],
            entry_price=order["price"],
        )
        logger.info(f"Opened position via {source}: {token.symbol} | size ${usd_size:.2f}")
        return pos

    def handle_copy_signal(self, signal: CopySignal) -> Optional[Position]:
        """Process a copy-trade signal from a tracked wallet."""
        can_open, reason = self.risk.can_open()
        if not can_open:
            logger.info(f"Cannot copy {signal.token_symbol}: {reason}")
            return None

        # Create a temporary TokenInfo for the copy
        token = TokenInfo(
            symbol=signal.token_symbol,
            address=signal.token_address,
            price=signal.entry_price,
            liquidity_usd=5000,  # We can improve this later
            dev_buy_pct=0,
            bundle_pct=0,
            top10_holders_pct=0,
            sniper_wallets=0,
        )

        # Use the size suggested by the copy manager
        usd_size = min(signal.amount_usd, self.risk.position_size())

        order = self.executor.buy(token.symbol, usd_size, token.price)
        if order["status"] != "filled":
            return None

        pos = self.risk.open_position(
            symbol=token.symbol,
            amount=order["amount"],
            entry_price=order["price"],
        )
        logger.info(
            f"COPY TRADE executed → Followed {signal.wallet_name} on {signal.token_symbol} | "
            f"${usd_size:.2f}"
        )
        return pos

    def check_position(self, symbol: str, current_price: float) -> None:
        signal = self.risk.check_exits(symbol, current_price)
        if not signal:
            return

        pos = self.risk.positions.get(symbol)
        if not pos:
            return

        if signal == "tp1":
            sell_amount = pos.amount * 0.5
            self.executor.sell(symbol, sell_amount, current_price)
            pos.amount -= sell_amount
            logger.info(f"TP1 hit on {symbol} – sold 50%")
        else:
            self.executor.sell(symbol, pos.amount, current_price)
            pnl = self.risk.close_position(symbol, current_price)
            logger.info(f"{signal.upper()} on {symbol} | PnL: ${pnl:.2f}")
