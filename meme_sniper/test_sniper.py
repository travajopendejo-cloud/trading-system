"""Quick test for the Meme Sniper (paper mode)."""

from core.sniper import MemeSniper
from core.filters import TokenInfo
import yaml
from pathlib import Path

def load_config():
    with open(Path(__file__).parent / "config.yaml") as f:
        return yaml.safe_load(f)

def main():
    config = load_config()
    sniper = MemeSniper(config)

    print("\n=== Meme Sniper Test ===")
    print(f"Capital: ${sniper.risk.capital:.2f}")
    print(f"Max position: ${sniper.risk.max_position_usd:.2f}\n")

    # Fake token that should pass filters
    good_token = TokenInfo(
        symbol="TESTCOIN",
        address="So11111111111111111111111111111111111111112",
        price=0.000012,
        liquidity_usd=2500,
        dev_buy_pct=4.5,
        bundle_pct=6.0,
        top10_holders_pct=22.0,
        sniper_wallets=8,
        age_seconds=12,
    )

    print("Evaluating good token...")
    pos = sniper.open_trade(good_token)

    if pos:
        print(f"→ Position opened: {pos.symbol} | Entry: {pos.entry_price}")
        print(f"  TP1: {pos.take_profit_1:.8f} | TP2: {pos.take_profit_2:.8f} | SL: {pos.stop_loss:.8f}")

        # Simulate price going up to TP1
        print("\nSimulating price rise to TP1...")
        sniper.check_position("TESTCOIN", pos.take_profit_1)

        # Simulate price going to TP2
        print("Simulating price rise to TP2...")
        sniper.check_position("TESTCOIN", pos.take_profit_2)

    print("\nTest finished.")

if __name__ == "__main__":
    main()
