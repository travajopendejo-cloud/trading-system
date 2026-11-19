"""Configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class RiskConfig(BaseModel):
    max_drawdown_pct: float = 15.0
    daily_loss_limit_pct: float = 5.0
    max_total_exposure_pct: float = 80.0
    max_open_positions: int = 12


class TelegramConfig(BaseModel):
    enabled: bool = False
    rate_limit_per_minute: int = 20
    notify_on: list[str] = Field(default_factory=lambda: ["trade_entry", "trade_exit", "error"])


class MetricsConfig(BaseModel):
    enabled: bool = True
    port: int = 8000
    path: str = "/metrics"


class GlobalConfig(BaseModel):
    exchange: dict[str, Any]
    trading: dict[str, Any]
    risk: RiskConfig
    capital_allocation: dict[str, float]
    logging: dict[str, Any]
    metrics: MetricsConfig
    telegram: TelegramConfig
    persistence: dict[str, Any]
    bots: list[dict[str, Any]]
    meme_sniper: dict[str, Any] = Field(default_factory=dict)


class BotRiskConfig(BaseModel):
    risk_per_trade_pct: float = 1.0
    stop_loss_atr_mult: float = 1.5
    take_profit_atr_mult: float = 2.5
    max_positions: int = 3
    trailing_stop: bool = False
    trailing_atr_mult: float = 1.5


class BotConfig(BaseModel):
    name: str
    strategy: str
    enabled: bool = True
    symbols: list[str]
    timeframe: str
    lookback_candles: int = 200
    parameters: dict[str, Any]
    risk: BotRiskConfig
    execution: dict[str, Any] = Field(default_factory=dict)


class Settings(BaseModel):
    """Environment-backed settings (secrets & runtime overrides)."""

    exchange_id: str = "binance"
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    trading_mode: str = "paper"  # paper | live | mock | demo
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = True
    metrics_port: int = 8000
    log_level: str = "INFO"
    log_json: bool = True


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_global_config(path: str = "config/global.yaml") -> GlobalConfig:
    data = load_yaml(path)
    return GlobalConfig(**data)


def load_bot_config(path: str) -> BotConfig:
    data = load_yaml(path)
    return BotConfig(**data)


def get_settings() -> Settings:
    """Load settings from environment variables (with dotenv support)."""
    def _bool(val: str, default: bool = True) -> bool:
        if val is None or val == "":
            return default
        return val.lower() in ("1", "true", "yes", "on")

    return Settings(
        exchange_id=os.getenv("EXCHANGE_ID", "binance"),
        api_key=os.getenv("API_KEY", ""),
        api_secret=os.getenv("API_SECRET", ""),
        passphrase=os.getenv("PASSPHRASE", ""),
        trading_mode=os.getenv("TRADING_MODE", "paper"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_enabled=_bool(os.getenv("TELEGRAM_ENABLED"), True),
        metrics_port=int(os.getenv("METRICS_PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_json=_bool(os.getenv("LOG_JSON"), True),
    )
