"""Config and environment helpers for the meme sniper."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from meme_sniper.core.constants import DEFAULT_PUBLIC_RPC


def load_sniper_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load meme_sniper/config.yaml and force paper mode."""
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config.yaml"
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        data = {}
    return apply_paper_only(data)


def apply_paper_only(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of config whose trading mode is paper."""
    safe = dict(config)
    safe["mode"] = "paper"
    return safe


def helius_api_key() -> str:
    """Read HELIUS_API_KEY. Empty string when it is not set."""
    return os.getenv("HELIUS_API_KEY", "").strip()


def solana_rpc_url() -> str:
    """Pick an RPC URL.

    SOLANA_RPC_URL wins. Otherwise a Helius RPC URL is built when
    HELIUS_API_KEY is set. With neither, the public Solana RPC is used.
    """
    explicit = os.getenv("SOLANA_RPC_URL", "").strip()
    if explicit:
        return explicit
    key = helius_api_key()
    if key:
        return f"https://mainnet.helius-rpc.com/?api-key={key}"
    public = os.getenv("SOLANA_PUBLIC_RPC_URL", "").strip()
    return public or DEFAULT_PUBLIC_RPC


def describe_rpc(url: str) -> str:
    """A log-safe description. API keys are never returned."""
    lowered = url.lower()
    if "api-key=" in lowered or "api_key=" in lowered or "apikey=" in lowered:
        if "helius" in lowered:
            return "Helius RPC (key hidden)"
        return "custom RPC (key hidden)"
    return url


def meme_sniper_enabled(config_flag: bool) -> bool:
    """MEME_SNIPER_ENABLED overrides the yaml flag when it is set."""
    raw = os.getenv("MEME_SNIPER_ENABLED", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return bool(config_flag)
