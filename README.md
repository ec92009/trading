# trading

Algorithmic paper trading bot using the [Alpaca](https://alpaca.markets) API.
All trades run against a **paper trading account** (no real money).

## Current docs

- [STRATEGY.md](/Users/ecohen/Dev/trading/STRATEGY.md): current sandbox strategy mechanics
- [RESULTS.md](/Users/ecohen/Dev/trading/RESULTS.md): research results, pitfalls, and current conclusion
- [TODO.md](/Users/ecohen/Dev/trading/TODO.md): active follow-up work
- [walk_forward_hourly_results.json](/Users/ecohen/Dev/trading/walk_forward_hourly_results.json): latest rolling walk-forward validation artifact
- [bot_refit_results.json](/Users/ecohen/Dev/trading/bot_refit_results.json): latest full-history production refit artifact, with an explicit do-not-auto-promote policy

Use this `README` for setup and operational scripts. For current strategy behavior and research conclusions, prefer the docs above.

Current live state:

- `bot.py` is still the 5-name basket bot
- `bot_10k.py` is now the Ro Khanna daily copy-trade bot
- the Khanna path refreshes Capitol Trades autonomously and stores visible disk cache under `_cache/`
- the Khanna bot now retries incomplete disclosure-driven orders during open-market heartbeats instead of waiting for a special end-of-day rebalance
- the intended deployment model is now a robust Python service (`RSCP`), not a compiled binary workflow

---

## What this project does

- Connects to an Alpaca paper trading account
- Places market orders (stocks and crypto) using fractional/notional amounts
- Runs an always-on background bot that monitors multiple positions concurrently and enforces trading rules automatically
- Persists order state in a local TSV log so restarts never duplicate buys, and reconciles fill prices/timestamps from Alpaca
- Writes a structured JSONL decision journal alongside the human-readable bot log
- Runs the current live posture as a rebalance-only bot on the existing 5-name basket
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
├── bot_10k.py        # Ro Khanna 10K live bot entrypoint
├── khanna_daily/     # Khanna live bot + market-data + signal-refresh helpers
├── portfolio.py      # View positions and pending orders
├── queue_orders.py   # Place multiple orders at once
├── dashboard.py      # Unified web control panel at http://localhost:8080
├── status.py         # matplotlib chart (interactive window or PNG)
├── _cache/           # Visible local cache root for hourly bars, daily bars, and politician refresh state
├── bot.log           # Live log output (not committed)
├── bot_decisions.jsonl # Structured decision + order-status journal (not committed)
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
| `python3 bot_10k.py` | Run the autonomous Ro Khanna 10K copy-trade bot |
| `python3 optimize_hourly_strategies.py` | Run the five-contender benchmark (`basket buy-and-hold`, `SPY`, `rebalance-only`, `stop/trigger`, `stop/trigger + rebalance`) on the `2023` train / `2024-2026Q1` holdout |
| `python3 refit_bot_strategy.py` | Refit the current strategy on all available history for live bot defaults |
| `python3 copytrade_demo.py` | Run the Capitol Trades copy-trade research script on the local signal file using the shared Alpaca cache and normalized active weights |

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

- the repo now assumes Alpaca is the live broker path for the foreseeable future
- fractional stock trading is treated as a normal default capability, not an edge-case experiment
- the live bot and the sandbox simulator are related but not identical, but the default simulator path now matches live stock sizing more closely by using fractional stock math
- the current sandbox mechanics are documented in [STRATEGY.md](/Users/ecohen/Dev/trading/STRATEGY.md)
- the latest research conclusions are documented in [RESULTS.md](/Users/ecohen/Dev/trading/RESULTS.md)

### Live behavior

| Behavior | Description |
|---|---|
| **Entry / sync** | On startup, flatten unmanaged positions, resume managed ones, and optionally do a startup rebalance |
| **Stop floor** | Present in research and config fields, but currently disabled in the live rebalance-only posture |
| **Trailing floor** | Present in research and config fields, but currently disabled in the live rebalance-only posture |
| **Cooldown** | Present in research and config fields, but currently inactive while the live bot stays rebalance-only |
| **Cash buffer** | Keep stop-sale and rebalance-sale proceeds in cash until rebalance redeploys them |
| **Rebalance** | Rebalance once per trading day, five minutes before the stock-market close, toward target weights |

### Khanna `10K` behavior

- `bot_10k.py` runs the Ro Khanna daily copy-trade book rather than the 5-name basket
- it refreshes Capitol Trades autonomously on startup and then every 15 minutes
- it still uses `copytrade_signals.json` as the local canonical signal file, but it now updates that file itself
- it stores market data and politician refresh metadata under `_cache/hourly_bars`, `_cache/daily_bars`, and `_cache/politicians`
- it now also maintains per-politician yearly signal caches under `_cache/politicians/<politician_slug>/<YYYY>/signals.json`
- it does not perform time-based portfolio rebalances outside disclosure changes; during market hours it only retries incomplete buys/sells from the active disclosure-driven target book
- incomplete order retries are capped at `5` attempts per asset and use versioned rationales like `BOT v51.4->Khanna copy-trade rebalance [attempt 2/5]`

### `10K` viewer

- the lightweight log viewer under [docs/](/Users/ecohen/Dev/trading/docs) now has four tabs:
- Runtime Log
- Decision Log
- Trade Journal
- Last Portfolio
- the Last Portfolio view shows asset-level target weight, current weight, derived point distribution, and current balance from the most recent committed snapshot

### Service posture

- the bot is still ordinary interpreted Python, not a compiled executable
- the operating assumption is an always-on machine or dedicated host, a pinned venv, and a supervised process
- the current direction is RSCP: robust Python service composition rather than packaging-first deployment

> **Note:** Alpaca does not support broker-native fractional stop orders for this setup. The logic is software-managed, so the bot must be running for stops, trails, and rebalances to happen.

### Restart safety

On startup the bot checks Alpaca positions **and** the local `trades.tsv` log before buying:
- **Existing filled position** → resume monitoring, cancel any duplicate open buy orders
- **Pending buy in TSV + still open on Alpaca** → keep it, resume with estimated entry
- **Pending buy in TSV + filled since last run** → update TSV, resume with actual fill price
- **Pending buy in TSV + cancelled on Alpaca** → mark TSV cancelled, place a fresh buy
- **No position, no TSV record** → fresh entry buy

The TSV log prevents duplicate buys if the bot restarts multiple times before a pending order has filled. It now also backfills `filled_at` and `avg_price` from Alpaca as orders complete, using human-readable UTC timestamps in both `trades.tsv` and `bot.log`.

### Market hours

- **Stocks:** the bot uses Alpaca's clock API to sleep until market open. No polling on nights, weekends, or market holidays.
- **Crypto:** crypto symbols remain eligible for off-hours risk monitoring, and the live bot now keeps `MANAGE_CRYPTO_24X7 = True`.

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
- Converts the requested dollar amount into a target weight using current portfolio size, writes that to `bot.py`, and reloads the launchd service on confirm
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
| `target_weight` | `0.20` | Portfolio target weight used by rebalance |
| `base_tol` | `0.0109` | Base beta-scaled floor distance |
| `trail_step` | `1.0235` | Re-raise floor every additional this % |
| `trail_stop` | `0.9885` | New floor = current price × this value |
| `stop_sell_pct` | `0.8342` | Fraction of a position sold on each stop hit |
| `stop_cooldown_days` | `5` | Trading-day cooldown after a stop |
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

### GitHub Pages log viewer

There is also a static log viewer under [`docs/`](/Users/ecohen/Dev/trading/docs) for GitHub Pages.
It now opens with four explicit tabs for:

- the runtime log
- the structured decision log
- the trade journal
- the last portfolio snapshot

Each tab renders the underlying `10k` bot file in a more human-readable format, and the running `10k` bot periodically publishes fresh committed snapshots into `docs/data/` so the latest view stays available on GitHub Pages even when you are away from the machine.
The page also shows the shared bot/app version badge sourced from the repo `VERSION`.
The Runtime Log `Show latest` control counts visible compacted UI entries, and the Trade Journal timing line now uses concise phrasing like `Executed in 1 s.` and `Filled immediately`.

To publish it on GitHub Pages:

```bash
git add docs README.md
git commit -m "Add GitHub Pages log viewer"
git push origin main
```

Then enable **Settings -> Pages -> Deploy from a branch** and choose:

- Branch: `main`
- Folder: `/docs`

After Pages finishes building, the viewer will be available at:

```text
https://ec92009.github.io/trading/
```

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
