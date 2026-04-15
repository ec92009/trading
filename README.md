# trading

Algorithmic paper trading bot using the [Alpaca](https://alpaca.markets) API.
All trades run against a **paper trading account** (no real money).

---

## What this project does

- Connects to an Alpaca paper trading account
- Places market orders (stocks and crypto) using fractional/notional amounts
- Runs an always-on background bot that monitors multiple positions concurrently and enforces trading rules automatically
- Displays a live visual dashboard of all positions

---

## Project structure

```
trading/
├── .env              # API credentials (not committed)
├── requirements.txt  # Python dependencies
├── bot.py            # Always-on trading bot (all assets, runs as launchd service)
├── main.py           # Check account balance
├── portfolio.py      # View positions and pending orders
├── queue_orders.py   # Place multiple orders at once
├── status.py         # Visual dashboard (price history + floor chart)
└── bot.log           # Live log output (not committed)
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- An [Alpaca paper trading account](https://app.alpaca.markets/paper/dashboard/overview)

### 2. Clone and install

```bash
git clone https://github.com/ec92009/trading.git
cd ~/Dev/trading
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure credentials

Create a `.env` file in the project root:

```
ALPACA_API_KEY=your_api_key_here
ALPACA_SECRET_KEY=your_secret_key_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

Get your paper trading API key from:
**Alpaca dashboard → Paper Trading → API Keys**

---

## Scripts

All scripts must be run from `~/Dev/trading` with the venv active:

```bash
cd ~/Dev/trading && source .venv/bin/activate
```

| Script | What it does |
|---|---|
| `python3 main.py` | Show account balance |
| `python3 portfolio.py` | Show positions and pending orders |
| `python3 queue_orders.py` | Place a batch of orders |
| `python3 status.py` | Open live visual dashboard |
| `SAVE_ONLY=1 python3 status.py` | Save dashboard to `status.png` |

---

## Trading Bot (`bot.py`)

A continuously running bot that manages multiple positions concurrently.
Each asset runs in its own thread with independent state.

### Trading rules (applied to every asset)

| Rule | Description |
|---|---|
| **Entry** | Buy initial notional at market on startup |
| **Stop loss (floor)** | Sell everything if price drops to entry × 0.95 |
| **Trailing floor** | Once price rises 10%, raise stop to current × 0.95. Re-raise every +5%. Floor only moves up. |
| **Ladder in — Level 1** | Buy more if price drops to floor × 0.925 |
| **Ladder in — Level 2** | Buy more if price drops to floor × 0.850 |

> **Note:** Alpaca does not support fractional stop orders. All rules are software-managed —
> the bot must be running for them to execute.

### Restart safety

On startup the bot checks for existing positions and pending orders before buying:
- **Existing position found** → resumes monitoring, cancels any stale pending buy orders
- **No position** → cancels stale orders, places a fresh entry buy

### Market hours

- **Stocks:** the bot uses Alpaca's clock API to sleep until market open (9:30 AM ET). No polling on nights, weekends, or holidays.
- **Crypto:** trades 24/7, no market hours guard.

---

## Adding an asset to watch

Open `bot.py` and find the `BOTS` list near the top:

```python
BOTS = [
    BotConfig(symbol="AAPL",    asset_class="stock"),
    BotConfig(symbol="BTC/USD", asset_class="crypto"),
]
```

Add a new `BotConfig` line for your asset:

```python
BOTS = [
    BotConfig(symbol="AAPL",    asset_class="stock"),
    BotConfig(symbol="BTC/USD", asset_class="crypto"),
    BotConfig(symbol="NVDA",    asset_class="stock"),   # ← new
    BotConfig(symbol="ETH/USD", asset_class="crypto"),  # ← new
]
```

**`asset_class` values:**
- `"stock"` — US equities (respects market hours, uses `DAY` orders)
- `"crypto"` — Crypto pairs like `BTC/USD`, `ETH/USD` (24/7, uses `GTC` orders)

**Optional per-asset overrides** — any `BotConfig` field can be customized:

```python
BotConfig(
    symbol="TSLA",
    asset_class="stock",
    initial_notional=100.0,   # buy $100 instead of $50
    ladder_notional=25.0,     # ladder in with $25 each time
    stop_pct=0.93,            # tighter stop: sell at entry × 0.93
    trail_trigger=1.15,       # start trailing after +15%
)
```

Full list of `BotConfig` fields:

| Field | Default | Description |
|---|---|---|
| `symbol` | required | Ticker (`"AAPL"`, `"BTC/USD"`, etc.) |
| `asset_class` | required | `"stock"` or `"crypto"` |
| `initial_notional` | `50.0` | Dollar amount for initial buy |
| `ladder_notional` | `50.0` | Dollar amount for each ladder-in buy |
| `stop_pct` | `0.95` | Sell all at entry × this value |
| `trail_trigger` | `1.10` | Activate trailing stop after this gain |
| `trail_step` | `1.05` | Re-raise floor every additional this % |
| `trail_stop` | `0.95` | New floor = current price × this value |
| `ladder1_pct` | `0.925` | First ladder buy at floor × this value |
| `ladder2_pct` | `0.850` | Second ladder buy at floor × this value |
| `poll_interval` | `30` | Seconds between price checks |

After editing, reload the background service:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.trading.bot.plist
launchctl load ~/Library/LaunchAgents/com.trading.bot.plist
```

---

## Background service (launchd)

The bot runs as a launchd agent — starts on login, restarts automatically on crash.

```bash
# Start
launchctl load ~/Library/LaunchAgents/com.trading.bot.plist

# Stop
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.trading.bot.plist

# Check running
launchctl list | grep trading

# Watch live log
tail -f ~/Dev/trading/bot.log
```

---

## Broker notes

| Feature | Supported |
|---|---|
| Fractional shares | Yes (notional market orders) |
| Crypto (BTC/USD, ETH/USD, etc.) | Yes, 24/7 |
| Fractional stop orders | No — use software-managed stops (this bot) |
| Extended hours trading | Limit orders only |
| Paper trading | Yes, free |
