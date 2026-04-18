# trading

Algorithmic paper trading bot using the [Alpaca](https://alpaca.markets) API.
All trades run against a **paper trading account** (no real money).

## Current docs

- [STRATEGY.md](/Users/ecohen/Dev/trading/STRATEGY.md): current sandbox strategy mechanics
- [RESULTS.md](/Users/ecohen/Dev/trading/RESULTS.md): research results, pitfalls, and current conclusion
- [TODO.md](/Users/ecohen/Dev/trading/TODO.md): active follow-up work
- [bot_refit_results.json](/Users/ecohen/Dev/trading/bot_refit_results.json): latest full-history production refit for live bot parameters

Use this `README` for setup and operational scripts. For current strategy behavior and research conclusions, prefer the docs above.

---

## What this project does

- Connects to an Alpaca paper trading account
- Places market orders (stocks and crypto) using fractional/notional amounts
- Runs an always-on background bot that monitors multiple positions concurrently and enforces trading rules automatically
- Persists order state in a local TSV log so restarts never duplicate buys
- Displays a live visual dashboard of all positions (unified HTML control panel or matplotlib chart)

---

## Project structure

```
trading/
├── .env              # API credentials (not committed)
├── requirements.txt  # Python dependencies
├── bot.py            # Always-on trading bot (all assets, runs as launchd service)
├── trade_log.py      # Thread-safe TSV log of pending/filled orders
├── add_asset.py      # TUI for adding a new asset to the bot
├── main.py           # Check account balance
├── portfolio.py      # View positions and pending orders
├── queue_orders.py   # Place multiple orders at once
├── dashboard.py      # Unified web control panel at http://localhost:8080
├── status.py         # matplotlib chart (interactive window or PNG)
├── .cache/           # Local raw hourly market-data cache (not committed)
├── bot.log           # Live log output (not committed)
└── trades.tsv        # Order state log (not committed)
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
| `python3 dashboard.py` | Launch unified control panel at http://localhost:8080 |
| `python3 dashboard.py --no-browser` | Launch control panel without auto-opening browser |
| `python3 dashboard.py --no-browser --port 8091` | Launch control panel on another port |
| `python3 status.py` | Open matplotlib chart window |
| `SAVE_ONLY=1 python3 status.py` | Save chart to `status.png` |
| `python3 add_asset.py` | TUI to add a new asset to the bot |
| `python3 optimize_hourly_strategies.py` | Run the benchmark `2023` train / `2024-2026Q1` holdout optimizer |
| `python3 refit_bot_strategy.py` | Refit the current strategy on all available history for live bot defaults |

---

## Trading Bot (`bot.py`)

A continuously running paper-trading bot that manages the current 5-name basket:

- `TSLA`
- `TSM`
- `NVDA`
- `PLTR`
- `BTC/USD`

Current live target weights:

- `TSLA`: `50%`
- `TSM`: `12.5%`
- `NVDA`: `12.5%`
- `PLTR`: `12.5%`
- `BTC/USD`: `12.5%`

Important:

- the live bot and the sandbox simulator are related but not identical
- the current sandbox mechanics are documented in [STRATEGY.md](/Users/ecohen/Dev/trading/STRATEGY.md)
- the latest research conclusions are documented in [RESULTS.md](/Users/ecohen/Dev/trading/RESULTS.md)

### Live behavior

| Behavior | Description |
|---|---|
| **Entry / sync** | On startup, flatten unmanaged positions, resume managed ones, and optionally do a startup rebalance |
| **Stop floor** | Use beta-scaled stop floors and sell `stop_sell_pct` of a position when price breaks the floor |
| **Trailing floor** | Raise the floor and next trigger as price moves up |
| **Cooldown** | After a stop, block another stop sale for `N` trading days |
| **BTC buffer** | Park stop-sale and rebalance-sale proceeds in BTC when possible |
| **Rebalance** | Rebalance once per trading day, five minutes before the stock-market close, toward target weights |

> **Note:** Alpaca does not support broker-native fractional stop orders for this setup. The logic is software-managed, so the bot must be running for stops, trails, and rebalances to happen.

### Restart safety

On startup the bot checks Alpaca positions **and** the local `trades.tsv` log before buying:
- **Existing filled position** → resume monitoring, cancel any duplicate open buy orders
- **Pending buy in TSV + still open on Alpaca** → keep it, resume with estimated entry
- **Pending buy in TSV + filled since last run** → update TSV, resume with actual fill price
- **Pending buy in TSV + cancelled on Alpaca** → mark TSV cancelled, place a fresh buy
- **No position, no TSV record** → fresh entry buy

The TSV log prevents duplicate buys if the bot restarts multiple times before a pending order has filled.

### Market hours

- **Stocks:** the bot uses Alpaca's clock API to sleep until market open. No polling on nights, weekends, or market holidays.
- **Crypto:** `BTC/USD` is in the basket and buffer accounting, but live `BTC` stop/trail management is currently still gated by `MANAGE_BTC_24X7 = False`.

---

## Adding an asset to watch

### Option 1 — TUI (recommended)

```bash
cd ~/Dev/trading && source .venv/bin/activate && python3 add_asset.py
```

An interactive terminal app will open showing your current cash and buying power.
Enter a symbol and investment amount (in `$` or `%` of buying power), confirm,
and the bot reloads automatically.

- Detects stock vs crypto automatically (`/` in symbol → crypto)
- Validates symbol isn't already watched, amount is positive and within buying power
- Writes to `bot.py` and reloads the launchd service on confirm
- Press `Escape` or **Cancel** to exit without changes

### Option 2 — Edit `bot.py` directly

Open `bot.py` and find the `BOTS` list near the top. The current setup uses explicit target weights:

```python
BOTS = [
    BotConfig(symbol="TSLA", asset_class="stock", target_weight=0.50),
    BotConfig(symbol="TSM", asset_class="stock", target_weight=0.125),
    BotConfig(symbol="NVDA", asset_class="stock", target_weight=0.125),
    BotConfig(symbol="PLTR", asset_class="stock", target_weight=0.125),
    BotConfig(symbol="BTC/USD", asset_class="crypto", target_weight=0.125),
]
```

Add a new `BotConfig` line for any asset you want to track.

**`asset_class` values:**
- `"stock"` — US equities (respects market hours, uses `DAY` orders)
- `"crypto"` — Crypto pairs like `BTC/USD`, `ETH/USD` (24/7, uses `GTC` orders)

**Optional per-asset overrides** — any `BotConfig` field can be customized:

```python
BotConfig(
    symbol="TSLA",
    asset_class="stock",
    target_weight=0.40,       # set a custom rebalance target
    base_tol=0.0040,          # widen or tighten beta-scaled floors
    stop_sell_pct=0.75,       # sell 75% on each stop hit
    stop_cooldown_days=4,     # wait 4 trading days before another stop sale
)
```

Full list of `BotConfig` fields:

| Field | Default | Description |
|---|---|---|
| `symbol` | required | Ticker (`"AAPL"`, `"BTC/USD"`, etc.) |
| `asset_class` | required | `"stock"` or `"crypto"` |
| `initial_notional` | `0.0` | Legacy field for initial buy sizing |
| `ladder_notional` | `0.0` | Legacy field, mostly superseded by rebalance sizing |
| `target_weight` | `0.20` | Portfolio target weight used by rebalance |
| `stop_pct` | `0.95` | Legacy field retained for compatibility |
| `trail_trigger` | `1.10` | Legacy field retained for compatibility |
| `trail_step` | `1.0321` | Re-raise floor every additional this % |
| `trail_stop` | `0.9879` | New floor = current price × this value |
| `base_tol` | `0.0035` | Base beta-scaled floor distance |
| `stop_sell_pct` | `0.8383` | Fraction of a position sold on each stop hit |
| `stop_cooldown_days` | `3` | Trading-day cooldown after a stop |
| `ladder1_pct` | `0.925` | Legacy field retained for compatibility |
| `ladder2_pct` | `0.850` | Legacy field retained for compatibility |
| `poll_interval` | `30` | Seconds between price checks |

After editing, reload the background service:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.trading.bot.plist
launchctl load ~/Library/LaunchAgents/com.trading.bot.plist
```

---

## Control panel

The main GUI is the unified HTML control panel in `dashboard.py`.
It combines:
- Alpaca credential setup
- Credential connection test
- Asset addition
- Asset editing and removal
- Launchd bot start / stop / reload controls
- LaunchAgent install / repair
- Live portfolio metrics and charts
- Open Alpaca orders
- Recent `trades.tsv` history
- Recent `bot.log` output

### Web control panel (`dashboard.py`) — recommended

```bash
python3 dashboard.py
```

Starts a local HTTP server at **http://localhost:8080** and opens your browser.
Use `--port 8091` or another port if `8080` is already in use.

The control panel shows one Chart.js panel per watched asset:
- Price line (color-coded per asset)
- Floor as a red dashed step function
- Y axis centered on entry price
- Live P&L badge per panel
- Portfolio total, cash, buying power, and P&L in the workspace header
- Recent bot log output for quick troubleshooting

Use `--no-browser` to skip auto-opening.

### Matplotlib dashboard (`status.py`)

The older matplotlib dashboard is still available if you want a local plot window or PNG output.

### matplotlib chart (`status.py`)

```bash
python3 status.py              # interactive window
SAVE_ONLY=1 python3 status.py  # save to status.png
```

Same layout but rendered via matplotlib. Useful for saving a snapshot PNG.

Both dashboards auto-load the current `BOTS` list from `bot.py` — no manual sync needed.

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
