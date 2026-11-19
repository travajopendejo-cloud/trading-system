"""Paper-mode tests for the meme sniper. No network."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from meme_sniper.core.constants import USDC, WSOL
from meme_sniper.core.executor import Executor
from meme_sniper.core.filters import TokenFilter, TokenInfo
from meme_sniper.core.http_client import TemporaryFetchError, classify_http_status
from meme_sniper.core.parsers import (
    ObservedBuy,
    looks_like_mint,
    normalize_price_impact_pct,
    parse_dexscreener_profiles,
    parse_helius_swap,
    parse_pump_coins,
    parse_wallet_transaction,
    token_info_from_dexscreener,
)
from meme_sniper.core.service import MemeSniperService
from meme_sniper.core.settings import (
    apply_paper_only,
    describe_rpc,
    load_sniper_config,
    meme_sniper_enabled,
    solana_rpc_url,
)
from meme_sniper.core.sniper import MemeSniper
from meme_sniper.core.wallet_copy import TRACKED_WALLETS, CopySignal, WalletCopyManager
from meme_sniper.core.wallet_monitor import WalletMonitor
from meme_sniper.core.wallet_copy import TrackedWallet


MINT = "Hyqxt1vPTpCmgWpSeYygyuRL25b5CBVuqWGAHpyYpump"
HERMES = "5dzH7gh5FjtrxUwtfBufJyTBA4fyCUGheZsdYQsE9vag"
NASEEM = "93WbteZH6nrWeHd5JT6mJE5VHbWcCTDZzzqqnpZ98V9G"


def safe_token(**overrides) -> TokenInfo:
    data = dict(
        symbol="TESTCOIN",
        address=MINT,
        price=1.0,
        liquidity_usd=80_000,
        graduated=True,
        dev_buy_pct=4.0,
        bundle_pct=6.0,
        top10_holders_pct=30.0,
        top_holder_pct=10.0,
        sniper_wallets=2,
        safety_checked=True,
        mint_revoked=True,
        freeze_revoked=True,
        holder_checked=True,
        sell_route_ok=True,
        sell_price_impact_pct=1.0,
        source="dexscreener",
    )
    data.update(overrides)
    return TokenInfo(**data)


class PassSafety:
    async def enrich(self, token):
        token.safety_checked = True
        token.mint_revoked = True
        token.freeze_revoked = True
        token.holder_checked = True
        token.top_holder_pct = 10.0
        token.top10_holders_pct = 30.0
        token.sell_route_ok = True
        token.sell_price_impact_pct = 1.0
        return token


class RejectSafety:
    async def enrich(self, token):
        token.safety_checked = True
        token.mint_revoked = False
        token.freeze_revoked = True
        token.sell_route_ok = True
        token.sell_price_impact_pct = 1.0
        return token


class FakeDiscovery:
    def __init__(self, tokens=None, lookup=None):
        self.tokens = list(tokens or [])
        self.lookup_token = lookup
        self.seen: set[str] = set()
        self.prices: dict[str, float] = {}

    async def poll(self):
        return list(self.tokens)

    async def lookup(self, mint, max_age_seconds=None):
        if self.lookup_token is not None and self.lookup_token.address == mint:
            return self.lookup_token
        return None

    async def price_usd(self, mint):
        if mint in self.prices:
            return self.prices[mint]
        if self.lookup_token is not None and self.lookup_token.address == mint:
            return self.lookup_token.price
        return None


class FakeWallets:
    def __init__(self, buys=None):
        self.buys = list(buys or [])
        self.helius_api_key = ""
        self.primed = True

    async def prime(self):
        return None

    async def poll(self):
        found = list(self.buys)
        self.buys.clear()
        return found


class BoomHttp:
    async def get_json(self, url):
        raise TemporaryFetchError("down")

    async def post_json(self, url, payload):
        raise TemporaryFetchError("down")


class QueueHttp:
    def __init__(self, gets=None, posts=None):
        self.gets = list(gets or [])
        self.posts = list(posts or [])

    async def get_json(self, url):
        return self._pop(self.gets)

    async def post_json(self, url, payload):
        return self._pop(self.posts)

    @staticmethod
    def _pop(queue):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_service(tokens=None, buys=None, safety=None, lookup=None) -> MemeSniperService:
    return MemeSniperService(
        load_sniper_config(),
        http=BoomHttp(),
        discovery=FakeDiscovery(tokens, lookup=lookup),
        wallets=FakeWallets(buys),
        safety=safety or PassSafety(),
    )


def rpc_tx(wallet, signer, deltas, err=None, signature="sig"):
    pre = []
    post = []
    for owner, mint, before, after in deltas:
        pre.append({"owner": owner, "mint": mint, "uiTokenAmount": {"uiAmount": before}})
        post.append({"owner": owner, "mint": mint, "uiTokenAmount": {"uiAmount": after}})
    return {
        "meta": {"err": err, "preTokenBalances": pre, "postTokenBalances": post},
        "transaction": {
            "signatures": [signature],
            "message": {"accountKeys": [{"pubkey": wallet, "signer": signer, "writable": True}]},
        },
        "blockTime": 1795120000,
    }


def test_tracked_wallets_match_on_chain_review():
    enabled = {wallet.name for wallet in TRACKED_WALLETS if wallet.enabled}
    disabled = {wallet.name for wallet in TRACKED_WALLETS if not wallet.enabled}
    assert enabled == {"Cented", "Decu", "Hermes"}
    assert disabled == {"Naseem", "Dingaling"}
    for wallet in TRACKED_WALLETS:
        assert looks_like_mint(wallet.address)
        assert wallet.notes
        if not wallet.checks_out:
            assert wallet.enabled is False
    assert next(wallet for wallet in TRACKED_WALLETS if wallet.name == "Naseem").checks_out is False
    manager = WalletCopyManager()
    assert NASEEM not in manager.wallets
    assert HERMES in manager.wallets


def test_filters_block_unsafe_tokens_and_allow_checked_ones():
    checker = TokenFilter(load_sniper_config())
    ok, reason = checker.is_valid(safe_token())
    assert ok is True
    assert reason == "Passed all filters"

    unchecked = safe_token(safety_checked=False)
    assert checker.is_valid(unchecked)[0] is False

    cases = [
        safe_token(mint_revoked=False),
        safe_token(freeze_revoked=False),
        safe_token(liquidity_usd=100),
        safe_token(top_holder_pct=80),
        safe_token(sell_route_ok=False),
        safe_token(sell_price_impact_pct=40),
        safe_token(price=0),
        safe_token(dev_buy_pct=20),
    ]
    for token in cases:
        assert checker.is_valid(token)[0] is False

    loose = TokenFilter({"filters": {"require_holder_data": False, "require_sell_route": True}})
    missing_holders = safe_token(holder_checked=False, top_holder_pct=0, top10_holders_pct=0)
    assert loose.is_valid(missing_holders)[0] is True
    strict = TokenFilter({"filters": {"require_holder_data": True}})
    assert strict.is_valid(missing_holders)[0] is False


def test_executor_cannot_leave_paper_mode():
    executor = Executor(mode="live")
    assert executor.mode == "paper"
    filled = executor.buy("TEST", 10, 1)
    assert filled["status"] == "filled"
    assert filled["mode"] == "paper"
    executor.mode = "live"
    rejected = executor.buy("TEST", 10, 1)
    assert rejected["status"] == "rejected"
    assert executor.sell("TEST", 1, 1)["status"] == "rejected"


def test_live_config_still_builds_a_paper_sniper():
    sniper = MemeSniper({"mode": "live"})
    assert sniper.executor.mode == "paper"
    assert apply_paper_only({"mode": "live"})["mode"] == "paper"
    assert load_sniper_config()["mode"] == "paper"


def test_unchecked_token_is_not_bought():
    sniper = MemeSniper(load_sniper_config())
    before = sniper.risk.capital
    assert sniper.open_trade(TokenInfo("RAW", MINT, 1, 5000)) is None
    assert sniper.risk.positions == {}
    assert sniper.risk.capital == before


def test_take_profit_sells_half_once_then_closes():
    sniper = MemeSniper(load_sniper_config())
    sells = []
    original = sniper.executor.sell

    def counting_sell(symbol, amount, price):
        sells.append((symbol, amount, price))
        return original(symbol, amount, price)

    sniper.executor.sell = counting_sell
    sniper.executor.cost_rate = 0
    pos = sniper.open_trade(safe_token())
    assert pos is not None
    assert abs(pos.amount - 10) < 1e-9
    sniper.check_position(pos.key, 2.0)
    assert len(sells) == 1
    assert abs(sells[0][1] - 10 / 3) < 1e-3
    still_open = sniper.risk.positions[pos.key]
    assert still_open.tp1_done is True
    sniper.check_position(pos.key, 2.0)
    assert len(sells) == 1
    sniper.check_position(pos.key, 4.0)
    sniper.check_position(pos.key, 8.0)
    assert pos.key not in sniper.risk.positions
    assert len(sniper.risk.closed_trades) == 3
    assert abs(sum(trade.pnl for trade in sniper.risk.closed_trades) - (110 / 3)) < 1e-3
    history = sniper.dashboard_trades()
    assert history[0]["bot"] == "meme_sniper"
    assert history[0]["side"] == "long"
    assert history[0]["pnl"] > 0


def test_time_stop_and_daily_loss_and_day_reset():
    sniper = MemeSniper(load_sniper_config())
    sniper.time_stop_minutes = 12
    sniper.executor.cost_rate = 0
    pos = sniper.open_trade(safe_token())
    pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    reason = sniper.check_position(pos.key, 1.1)
    assert reason == "time"
    assert sniper.risk.positions == {}
    assert sniper.risk.closed_trades[-1].reason == "time"

    sniper.risk.daily_pnl = -25
    assert sniper.risk.can_open()[0] is False
    assert sniper.risk.halted is True
    sniper.risk.daily_reset = datetime.now(timezone.utc).date() - timedelta(days=1)
    assert sniper.risk.can_open()[0] is True
    assert sniper.risk.halted is False


def test_copy_uses_filters_and_ignores_disabled_wallets():
    sniper = MemeSniper(load_sniper_config())
    buys = []
    original = sniper.executor.buy

    def counting_buy(*args, **kwargs):
        buys.append(args)
        return original(*args, **kwargs)

    sniper.executor.buy = counting_buy
    bad = CopySignal("Naseem", NASEEM, MINT, "TEST", 1, 10)
    assert sniper.handle_copy_signal(bad, safe_token()) is None
    unsafe = safe_token(mint_revoked=False)
    good_signal = CopySignal("Hermes", HERMES, MINT, "TESTCOIN", 1, 10)
    assert sniper.handle_copy_signal(good_signal, unsafe) is None
    assert buys == []
    opened = sniper.handle_copy_signal(good_signal, safe_token())
    assert opened is not None
    assert opened.source == "copy:Hermes"
    assert len(buys) == 1

    manager = sniper.wallet_copy
    assert manager.should_copy("not-a-wallet", MINT, "T", 1, 100) is None
    assert manager.should_copy(HERMES, MINT, "T", 1, 10) is None
    assert manager.should_copy(HERMES, MINT, "T", 1, 80) is None
    manager.wallets[HERMES].avg_hold_minutes = 45
    signal = manager.should_copy(HERMES, MINT, "T", 1, 80)
    assert signal is not None
    assert signal.amount_usd <= 10


def test_parsers_keep_real_buys_and_drop_airdrops_sells_and_bystanders():
    wallet = "Wallet111"
    buy = rpc_tx(
        wallet,
        True,
        [
            (wallet, USDC, 100, 80),
            (wallet, MINT, 0, 5_000_000),
        ],
        signature="buy-sig",
    )
    parsed = parse_wallet_transaction(buy, wallet)
    assert len(parsed) == 1
    assert parsed[0].token_address == MINT
    assert parsed[0].quote_amount == 20
    assert parsed[0].signature == "buy-sig"

    airdrop = rpc_tx(wallet, True, [(wallet, MINT, 0, 1)])
    assert parse_wallet_transaction(airdrop, wallet) == []
    sell = rpc_tx(wallet, True, [(wallet, USDC, 10, 40), (wallet, MINT, 1000, 0)])
    assert parse_wallet_transaction(sell, wallet) == []
    bystander = rpc_tx(wallet, False, [(wallet, USDC, 100, 80), (wallet, MINT, 0, 5000)])
    assert parse_wallet_transaction(bystander, wallet) == []
    failed = rpc_tx(wallet, True, [(wallet, USDC, 100, 80), (wallet, MINT, 0, 5000)], err="failed")
    assert parse_wallet_transaction(failed, wallet) == []

    helius_buy = {
        "type": "SWAP",
        "signature": "helius-buy",
        "timestamp": 1795120000,
        "feePayer": wallet,
        "events": {
            "swap": {
                "tokenInputs": [{
                    "mint": USDC,
                    "userAccount": wallet,
                    "rawTokenAmount": {"tokenAmount": "200000000", "decimals": 6},
                }],
                "tokenOutputs": [{
                    "mint": MINT,
                    "userAccount": wallet,
                    "rawTokenAmount": {"tokenAmount": "23000000000000", "decimals": 6},
                }],
            }
        },
    }
    helius = parse_helius_swap(helius_buy, wallet)
    assert len(helius) == 1
    assert helius[0].quote_amount == 200
    assert helius[0].token_amount == 23_000_000
    someone_else = dict(helius_buy, feePayer="other-wallet")
    assert parse_helius_swap(someone_else, wallet) == []
    airdrop_transfer = {
        "type": "TRANSFER",
        "signature": "dust",
        "feePayer": wallet,
        "tokenTransfers": [{
            "fromUserAccount": "airdropper",
            "toUserAccount": wallet,
            "mint": MINT,
            "tokenAmount": 1,
        }],
    }
    assert parse_helius_swap(airdrop_transfer, wallet) == []
    sol_buy = {
        "type": "SWAP",
        "signature": "sol-buy",
        "feePayer": wallet,
        "events": {
            "swap": {
                "nativeInput": {"amount": "500000000"},
                "tokenOutputs": [{
                    "mint": MINT,
                    "userAccount": wallet,
                    "rawTokenAmount": {"tokenAmount": "10000000000", "decimals": 6},
                }],
            }
        },
    }
    sol_parsed = parse_helius_swap(sol_buy, wallet)
    assert len(sol_parsed) == 1
    assert sol_parsed[0].quote_mint == WSOL
    assert sol_parsed[0].quote_amount == 0.5


def test_discovery_parsers_and_price_impact():
    assert normalize_price_impact_pct("0.0108") == 1.08
    assert normalize_price_impact_pct(12) == 12
    assert normalize_price_impact_pct(None) is None
    assert classify_http_status(200) == "ok"
    assert classify_http_status(429) == "temporary"
    assert classify_http_status(503) == "temporary"
    assert classify_http_status(404) == "permanent"

    profiles = [
        {"chainId": "solana", "tokenAddress": MINT},
        {"chainId": "ethereum", "tokenAddress": "0xabc"},
        {"chainId": "solana", "tokenAddress": "not a mint"},
    ]
    assert parse_dexscreener_profiles(profiles) == [MINT]
    assert parse_pump_coins([{"mint": MINT, "symbol": "X"}]) == [MINT]
    assert parse_pump_coins({"coins": [{"address": MINT}]}) == [MINT]

    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    fresh = int(datetime(2026, 11, 18, 23, 30, tzinfo=timezone.utc).timestamp() * 1000)
    stale = int(datetime(2026, 11, 18, tzinfo=timezone.utc).timestamp() * 1000)
    payload = {
        "pairs": [
            {
                "chainId": "solana",
                "dexId": "pumpswap",
                "pairCreatedAt": fresh,
                "priceUsd": "0.0002",
                "liquidity": {"usd": 12000},
                "baseToken": {"address": MINT, "symbol": "HELLO", "name": "Hello"},
                "quoteToken": {"address": USDC, "symbol": "USDC"},
            },
            {
                "chainId": "solana",
                "pairCreatedAt": fresh,
                "priceUsd": "0.0001",
                "liquidity": {"usd": 100},
                "baseToken": {"address": MINT, "symbol": "HELLO"},
                "quoteToken": {"address": WSOL, "symbol": "SOL"},
            },
        ]
    }
    token = token_info_from_dexscreener(payload, MINT, max_age_seconds=3 * 3600, now=now)
    assert token is not None
    assert token.symbol == "HELLO"
    assert token.liquidity_usd == 12000
    assert token.price == 0.0002
    old_payload = {"pairs": [{**payload["pairs"][0], "pairCreatedAt": stale}]}
    assert token_info_from_dexscreener(old_payload, MINT, max_age_seconds=3600, now=now) is None


def test_wallet_monitor_copies_only_new_signer_buys_and_survives_outages():
    wallet = TrackedWallet("Wallet111", "Test", enabled=True, checks_out=True)
    buy = rpc_tx(
        "Wallet111",
        True,
        [("Wallet111", USDC, 50, 20), ("Wallet111", MINT, 0, 9_000)],
        signature="new-sig",
    )
    http = QueueHttp(posts=[
        {"result": [{"signature": "old-sig", "err": None}]},
        {"result": [
            {"signature": "new-sig", "err": None},
            {"signature": "old-sig", "err": None},
            {"signature": "bad-sig", "err": "failed"},
        ]},
        {"result": buy},
    ])
    monitor = WalletMonitor([wallet], http, "http://rpc.test", helius_api_key="")
    asyncio.run(monitor.prime())
    found = asyncio.run(monitor.poll())
    assert [item.signature for item in found] == ["new-sig"]
    assert http.posts == []

    helius_old = {
        "type": "SWAP",
        "signature": "old",
        "feePayer": "Wallet111",
        "events": {"swap": {
            "tokenInputs": [{"mint": USDC, "userAccount": "Wallet111", "rawTokenAmount": {"tokenAmount": "30000000", "decimals": 6}}],
            "tokenOutputs": [{"mint": MINT, "userAccount": "Wallet111", "rawTokenAmount": {"tokenAmount": "5000000000", "decimals": 6}}],
        }},
    }
    helius_new = dict(helius_old, signature="new")
    helius_http = QueueHttp(gets=[[helius_old], [helius_new, helius_old]])
    helius_monitor = WalletMonitor(
        [wallet], helius_http, "http://rpc.test", helius_api_key="test-key"
    )
    asyncio.run(helius_monitor.prime())
    helius_found = asyncio.run(helius_monitor.poll())
    assert [item.signature for item in helius_found] == ["new"]

    down = WalletMonitor([wallet], BoomHttp(), "http://rpc.test", helius_api_key="")
    asyncio.run(down.prime())
    assert asyncio.run(down.poll()) == []
    assert down.primed == set()


def test_low_liquidity_never_reaches_the_safety_check():
    class CountingSafety(PassSafety):
        def __init__(self):
            self.calls = 0

        async def enrich(self, token):
            self.calls += 1
            return await PassSafety.enrich(self, token)

    safety = CountingSafety()
    token = safe_token(liquidity_usd=10, symbol="DUST")
    service = make_service(tokens=[token], safety=safety)
    asyncio.run(service.cycle())
    assert safety.calls == 0
    assert service.sniper.risk.positions == {}
    assert token.address in service.discovery.seen


def test_service_paper_trades_safe_tokens_and_ignores_network_loss():
    token = safe_token()
    service = make_service(tokens=[token])
    asyncio.run(service.cycle())
    assert token.address in service.sniper.risk.positions
    assert token.address in service.discovery.seen
    position = service.sniper.risk.positions[token.address]
    assert position.source == "dexscreener"

    rejected = make_service(tokens=[safe_token(symbol="BAD")], safety=RejectSafety())
    asyncio.run(rejected.cycle())
    assert rejected.sniper.risk.positions == {}

    copy_token = safe_token(symbol="COPIED", price=0.0002, liquidity_usd=80_000)
    buy = ObservedBuy(
        wallet_address=HERMES,
        token_address=MINT,
        token_amount=1_000_000,
        quote_mint=USDC,
        quote_amount=200,
        signature="copy-sig",
    )
    copier = make_service(buys=[buy], lookup=copy_token)
    copier.sniper.wallet_copy.wallets[HERMES].avg_hold_minutes = 45
    asyncio.run(copier.cycle())
    copied = copier.sniper.risk.positions[MINT]
    assert copied.source == "copy:Hermes"
    assert copied.symbol == "COPIED"

    unsafe_copy = make_service(buys=[buy], lookup=copy_token, safety=RejectSafety())
    asyncio.run(unsafe_copy.cycle())
    assert unsafe_copy.sniper.risk.positions == {}
    assert unsafe_copy.sniper.wallet_copy.recent_signals == []

    dark = MemeSniperService(load_sniper_config(), http=BoomHttp())
    asyncio.run(dark.cycle())
    assert dark.sniper.risk.positions == {}
    assert dark.sniper.executor.mode == "paper"


def test_rpc_and_enable_flags_hide_secrets(monkeypatch):
    monkeypatch.delenv("HELIUS_API_KEY", raising=False)
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    monkeypatch.delenv("MEME_SNIPER_ENABLED", raising=False)
    assert "api.mainnet-beta.solana.com" in solana_rpc_url()
    assert meme_sniper_enabled(True) is True
    monkeypatch.setenv("HELIUS_API_KEY", "super-secret")
    hidden = solana_rpc_url()
    assert "super-secret" in hidden
    assert "super-secret" not in describe_rpc(hidden)
    monkeypatch.setenv("SOLANA_RPC_URL", "https://example.invalid/rpc")
    assert solana_rpc_url() == "https://example.invalid/rpc"
    monkeypatch.setenv("MEME_SNIPER_ENABLED", "false")
    assert meme_sniper_enabled(True) is False
    monkeypatch.setenv("MEME_SNIPER_ENABLED", "true")
    assert meme_sniper_enabled(False) is True


def test_dashboard_status_includes_sniper_history_and_stays_importable():
    import main  # noqa: F401
    from core.config import load_global_config
    from core.dashboard import DASHBOARD_HTML
    from core.orchestrator import Orchestrator

    config = load_global_config("config/global.yaml")
    assert config.meme_sniper["enabled"] is True
    token = safe_token()
    service = make_service(tokens=[token])
    asyncio.run(service.cycle())
    position = next(iter(service.sniper.risk.positions.values()))
    service.sniper.check_position(position.key, position.take_profit_2)

    orchestrator = Orchestrator(config)
    status = orchestrator.get_status()
    assert "Cented" in status["sniper"]["tracked_wallets"]
    assert "Naseem" in status["sniper"]["disabled_wallets"]
    assert "Dingaling" in status["sniper"]["disabled_wallets"]
    assert status["sniper"]["mode"] == "paper"
    assert status["closed_trades"] == []

    orchestrator.meme_sniper = service
    status = orchestrator.get_status()
    trades = [row for row in status["closed_trades"] if row["bot"] == "meme_sniper"]
    assert trades
    assert trades[0]["symbol"] == "TESTCOIN"
    assert any(row["pnl"] > 0 for row in trades)
    assert "id=\"sniper-line\"" in DASHBOARD_HTML
    assert "function fmtPx" in DASHBOARD_HTML
