# Multi-Bot Production Trading System

Production-oriented multi-strategy crypto trading system written in Python.

**Features**
- 3 independent bots (Trend, Mean-Reversion, Breakout) with isolated capital & risk
- Central Orchestrator with portfolio-level risk controls
- Paper trading (default) and live mode
- Docker support
- Telegram real-time alerts
- Prometheus metrics (`/metrics`)
- Backtesting engine
- Structured logging, graceful shutdown, retries, rate-limit handling

> **Disclaimer**  
> This software is for **educational and research purposes only**.  
> It is **not** financial advice. Trading cryptocurrencies involves substantial risk of loss.  
> Never risk money you cannot afford to lose. Past performance is not indicative of future results.  
> Use at your own risk. The authors assume no liability for any losses.

---

## Quick Start (Paper Trading)

```bash
# 1. Clone / enter project
cd trading_system

# 2. Create virtualenv
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# Edit .env – leave API keys empty for pure paper mode

# 5. Run
python main.py
```

The system will start all enabled bots in **paper mode**, expose metrics on `http://localhost:8000/metrics`, and log to console + `logs/`.

---

## Docker

```bash
# Build & run
cd trading_system
docker compose -f docker/docker-compose.yml up --build -d

# View logs
docker compose -f docker/docker-compose.yml logs -f

# Stop
docker compose -f docker/docker-compose.yml down
```

---

## Configuration

| File | Purpose |
|------|---------|
| `config/global.yaml` | Exchange, global risk, capital allocation, which bots to run |
| `config/trend_bot.yaml` | Trend bot symbols, timeframe, parameters, risk |
| `config/mean_reversion_bot.yaml` | Mean-reversion bot settings |
| `config/breakout_bot.yaml` | Breakout bot settings |
| `.env` | Secrets (API keys, Telegram token) and runtime overrides |

### Capital Allocation
In `global.yaml`:
```yaml
capital_allocation:
  trend_bot: 0.35
  mean_reversion_bot: 0.35
  breakout_bot: 0.30
```

### Enabling / Disabling Bots
```yaml
bots:
  - name: trend_bot
    enabled: true
    config_file: config/trend_bot.yaml
```

---

## Telegram Alerts

1. Create a bot via [@BotFather](https://t.me/BotFather) and get the token.
2. Get your chat ID (e.g. via `@userinfobot`).
3. Set in `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=987654321
   TELEGRAM_ENABLED=true
   ```

You will receive notifications for entries, exits, stop-losses, take-profits, errors, and daily summaries.

---

## Prometheus Metrics

When running, metrics are available at:

```
http://localhost:8000/metrics
```

Key metrics:
- `trading_equity{scope="total|bot_name"}`
- `trading_unrealized_pnl`
- `trading_open_positions`
- `trading_trades_total{bot, result}`
- `trading_drawdown_pct`
- `trading_order_latency_seconds`
- `trading_bot_status{bot}`
- `trading_errors_total`

---

## Backtesting

Example (run from project root):

```python
import pandas as pd
from backtest.engine import BacktestEngine
from strategies.trend import TrendStrategy
from core.config import load_bot_config

# Load historical OHLCV (timestamp, open, high, low, close, volume)
df = pd.read_csv("data/BTCUSDT_15m.csv")
df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

cfg = load_bot_config("config/trend_bot.yaml")
strategy = TrendStrategy(params={**cfg.parameters, **cfg.risk.model_dump()})

engine = BacktestEngine(initial_capital=10_000, fee_rate=0.0004)
result = engine.run(
    strategy=strategy,
    data={"BTC/USDT:USDT": df},
    risk_per_trade_pct=cfg.risk.risk_per_trade_pct,
)

print(result.metrics)
print(f"Final equity: {result.equity_curve.iloc[-1]:.2f}")
```

---

## Switching to Live Mode

1. Set `TRADING_MODE=live` in `.env`
2. Provide valid `API_KEY` and `API_SECRET`
3. Set `sandbox: false` in `config/global.yaml` (or use exchange testnet first)
4. Start with **very small** capital allocation and monitor closely
5. Prefer testnet / paper for several weeks before any real capital

---

## Project Structure

```
trading_system/
├── main.py                 # Entrypoint
├── core/
│   ├── orchestrator.py     # Portfolio manager
│   ├── exchange.py         # ccxt wrapper
│   ├── risk.py             # Risk engine
│   ├── metrics.py          # Prometheus
│   ├── telegram_notifier.py
│   ├── config.py
│   └── logging_setup.py
├── bots/
│   ├── base_bot.py
│   ├── trend_bot.py
│   ├── mean_reversion_bot.py
│   └── breakout_bot.py
├── strategies/
│   ├── trend.py
│   ├── mean_reversion.py
│   └── breakout.py
├── backtest/
│   └── engine.py
├── config/
├── docker/
├── data/
└── logs/
```

---

## Safety Features

- Portfolio max drawdown circuit breaker
- Daily loss limit
- Max total exposure
- Per-bot max positions
- Fixed-fractional position sizing
- Paper mode by default
- Graceful shutdown on SIGINT/SIGTERM
- Automatic retries with exponential backoff on network errors

---

## License

MIT (or your preferred license)

**Use responsibly.**
