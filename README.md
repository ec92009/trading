# trading

Algorithmic paper trading bot using the [Alpaca](https://alpaca.markets) API.
All trades run against a **paper trading account** (no real money).

## Current docs

- [STRATEGY.md](/Users/ecohen/Dev/trading/STRATEGY.md): current sandbox strategy mechanics
- [RESULTS.md](/Users/ecohen/Dev/trading/RESULTS.md): research results, pitfalls, and current conclusion
- [TODO.md](/Users/ecohen/Dev/trading/TODO.md): active follow-up work

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

---

## Trading Bot (`bot.py`)

A continuously running bot that manages multiple positions concurrently.
Each asset runs in its own thread with independent state.

Important:

- the live bot and the sandbox simulator are related but not identical
- the current research strategy is documented in [STRATEGY.md](/Users/ecohen/Dev/trading/STRATEGY.md)
- the latest research conclusions are documented in [RESULTS.md](/Users/ecohen/Dev/trading/RESULTS.md)

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

On startup the bot checks Alpaca positions **and** the local `trades.tsv` log before buying:
- **Existing filled position** → resume monitoring, cancel any duplicate open buy orders
- **Pending buy in TSV + still open on Alpaca** → keep it, resume with estimated entry
- **Pending buy in TSV + filled since last run** → update TSV, resume with actual fill price
- **Pending buy in TSV + cancelled on Alpaca** → mark TSV cancelled, place a fresh buy
- **No position, no TSV record** → fresh entry buy

The TSV log prevents duplicate buys if the bot restarts multiple times before a pending order has filled.

### Market hours

- **Stocks:** the bot uses Alpaca's clock API to sleep until market open (9:30 AM ET). No polling on nights, weekends, or holidays.
- **Crypto:** trades 24/7, no market hours guard.

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

Open `bot.py` and find the `BOTS` list near the top. A typical setup sizes each position as a fraction of total portfolio value:

```python
_P = 348.71   # portfolio value at last rebalance
BOTS = [
    BotConfig(symbol="AAPL",    asset_class="stock",  initial_notional=round(_P*0.10, 2), ladder_notional=round(_P*0.10, 2)),
    BotConfig(symbol="BTC/USD", asset_class="crypto", initial_notional=round(_P*0.10, 2), ladder_notional=round(_P*0.10, 2)),
    BotConfig(symbol="PLTR",    asset_class="stock",  initial_notional=round(_P*0.20, 2), ladder_notional=round(_P*0.20, 2)),
    BotConfig(symbol="NVDA",    asset_class="stock",  initial_notional=round(_P*0.20, 2), ladder_notional=round(_P*0.20, 2)),
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
