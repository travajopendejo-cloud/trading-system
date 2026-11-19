"""Paper-only Solana meme sniper.

Live trading is intentionally not implemented. Importing this package
does not open wallets, send transactions, or read private keys.
"""

from meme_sniper.core.settings import load_sniper_config

__all__ = ["load_sniper_config"]
