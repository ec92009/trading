# trading

Algorithmic paper trading bot using the [Alpaca](https://alpaca.markets) API.
All trades run against a **paper trading account** (no real money).

---

## What this project does

- Connects to an Alpaca paper trading account
- Places market orders (stocks and crypto) using fractional/notional amounts
- Runs an always-on background bot (`aapl_bot.py`) that monitors a position and enforces trading rules automatically

---

## Project structure

```
trading/
├── .env                  # API credentials (not committed)
├── requirements.txt      # Python dependencies
├── main.py               # Check account balance
├── portfolio.py          # View positions and pending orders
├── queue_orders.py       # Place multiple orders at once
├── replace_pltr.py       # Cancel + replace a specific order
├── buy_pltr.py           # One-off buy script (example)
├── aapl_bot.py           # Always-on trading bot (see below)
├── aapl_bot_preview.py   # Preview bot setup without starting monitor loop
└── aapl_bot.log          # Bot log output (not committed)
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- An [Alpaca paper trading account](https://app.alpaca.markets/paper/dashboard/overview)

### 2. Clone and install

```bash
git clone https://github.com/ec92009/trading.git
cd trading
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

### Check account balance
```bash
python3 main.py
```

### View portfolio + pending orders
```bash
python3 portfolio.py
```

### Place multiple orders
Edit `queue_orders.py` to define your orders, then:
```bash
python3 queue_orders.py
```

---

## AAPL Bot (`aapl_bot.py`)

A continuously running bot that manages a single AAPL position with four rules:

### Rules

| Rule | Description |
|---|---|
| **Entry** | Buy $50 of AAPL at market on startup |
| **Stop loss (floor)** | Sell everything if price drops to entry × 0.95 |
| **Trailing floor** | Once price rises 10%, move stop to current × 0.95. Re-raise every +5%. Floor only moves up. |
| **Ladder in — Level 1** | Buy $50 more if price drops to floor × 0.925 |
| **Ladder in — Level 2** | Buy $50 more if price drops to floor × 0.850 |

> **Note:** Alpaca does not support fractional stop orders. All rules are implemented in software — the bot must be running for them to execute.

### Market hours

The bot uses Alpaca's clock API to detect market hours. When the market is closed it sleeps until the next open — no polling on nights, weekends, or holidays.

### Run manually
```bash
source .venv/bin/activate
python3 aapl_bot.py
```

### Run as a background service (launchd, Mac)

A launchd plist is included at:
```
~/Library/LaunchAgents/com.trading.aapl-bot.plist
```

Load it:
```bash
launchctl load ~/Library/LaunchAgents/com.trading.aapl-bot.plist
```

The bot will now start automatically on login and restart if it crashes.

```bash
# Stop
launchctl unload ~/Library/LaunchAgents/com.trading.aapl-bot.plist

# Check status
launchctl list | grep aapl

# View live log
tail -f ~/Dev/trading/aapl_bot.log
```

---

## Broker notes

| Feature | Supported |
|---|---|
| Fractional shares | Yes (market orders only) |
| Crypto (BTC/USD etc.) | Yes, 24/7 |
| Fractional stop orders | No — use software-managed stops |
| Extended hours trading | Limit orders only |
| Paper trading | Yes, free |
