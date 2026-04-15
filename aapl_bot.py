"""
AAPL trading bot.
- Buys $50 of AAPL at market
- Stop loss at entry * 0.95 (floor)
- Trailing floor: after +10% gain, stop moves to current * 0.95; re-raises every +5%
- Ladder in: buy $50 more at floor * 0.925, buy $50 more at floor * 0.85
- All stop/ladder logic is software-managed (Alpaca doesn't support fractional stop orders)
- Market hours guard: sleeps until next market open when closed
"""

import os
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

# Load .env from the same directory as this script
load_dotenv(Path(__file__).parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────

SYMBOL          = "AAPL"
INITIAL_NOTIONAL = 50.0
LADDER_NOTIONAL  = 50.0

STOP_PCT         = 0.95    # floor: sell all if price drops to entry * 0.95
TRAIL_TRIGGER    = 1.10    # start trailing after +10% gain
TRAIL_STEP       = 1.05    # re-raise every additional +5%
TRAIL_STOP       = 0.95    # new stop = current price * 0.95

LADDER1_PCT      = 0.925   # buy more at floor * 0.925
LADDER2_PCT      = 0.85    # buy more at floor * 0.850

POLL_INTERVAL    = 30      # seconds between price checks
LOG_FILE         = Path(__file__).parent / "aapl_bot.log"

# ── Clients ───────────────────────────────────────────────────────────────────

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

trading = TradingClient(
    api_key=os.getenv("ALPACA_API_KEY"),
    secret_key=os.getenv("ALPACA_SECRET_KEY"),
    paper=True,
)
data = StockHistoricalDataClient(
    api_key=os.getenv("ALPACA_API_KEY"),
    secret_key=os.getenv("ALPACA_SECRET_KEY"),
)

# ── State ─────────────────────────────────────────────────────────────────────

@dataclass
class BotState:
    entry_price:      float = 0.0
    floor:            float = 0.0
    ladder1_price:    float = 0.0
    ladder2_price:    float = 0.0
    trail_trigger:    float = 0.0   # price at which we next raise the floor
    total_qty:        float = 0.0
    ladder1_filled:   bool  = False
    ladder2_filled:   bool  = False
    stopped_out:      bool  = False
    orders:           list  = field(default_factory=list)

state = BotState()

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_mid_price() -> float:
    q = data.get_stock_latest_quote(
        StockLatestQuoteRequest(symbol_or_symbols=SYMBOL)
    )[SYMBOL]
    ask = float(q.ask_price or 0)
    bid = float(q.bid_price or 0)
    if ask and bid:
        return (ask + bid) / 2
    return ask or bid

def buy(notional: float, reason: str) -> str:
    order = trading.submit_order(MarketOrderRequest(
        symbol=SYMBOL,
        notional=notional,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    ))
    qty_est = round(notional / state.entry_price, 6) if state.entry_price else 0
    state.total_qty += qty_est
    entry = {"reason": reason, "side": "BUY", "notional": notional,
             "order_id": str(order.id), "status": str(order.status),
             "submitted_at": datetime.now(timezone.utc).isoformat()}
    state.orders.append(entry)
    log(f"BUY ${notional} {SYMBOL} [{reason}] → {order.status} id={order.id}")
    return str(order.id)

def sell_all(reason: str):
    order = trading.submit_order(MarketOrderRequest(
        symbol=SYMBOL,
        qty=round(state.total_qty, 6),
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    ))
    entry = {"reason": reason, "side": "SELL", "qty": state.total_qty,
             "order_id": str(order.id), "status": str(order.status),
             "submitted_at": datetime.now(timezone.utc).isoformat()}
    state.orders.append(entry)
    state.stopped_out = True
    log(f"SELL ALL {state.total_qty} {SYMBOL} [{reason}] → {order.status} id={order.id}")

def log(msg: str):
    logger.info(msg)

def wait_for_market_open():
    """Block until market is open. Returns immediately if already open."""
    clock = trading.get_clock()
    if clock.is_open:
        return
    now = datetime.now(timezone.utc)
    next_open = clock.next_open.replace(tzinfo=timezone.utc)
    secs = max(0, (next_open - now).total_seconds())
    log(f"Market closed. Sleeping {secs/3600:.1f}h until next open "
        f"({clock.next_open.strftime('%Y-%m-%d %H:%M %Z')})")
    time.sleep(secs + 5)   # +5s buffer for market open lag

# ── Setup ─────────────────────────────────────────────────────────────────────

def setup():
    price = get_mid_price()
    state.entry_price  = price
    state.floor        = round(price * STOP_PCT, 2)
    state.ladder1_price = round(state.floor * LADDER1_PCT, 2)
    state.ladder2_price = round(state.floor * LADDER2_PCT, 2)
    state.trail_trigger = round(price * TRAIL_TRIGGER, 2)
    state.total_qty    = round(INITIAL_NOTIONAL / price, 6)

    buy(INITIAL_NOTIONAL, "initial entry")

    print("\n" + "=" * 60)
    print(f"  AAPL BOT — SETUP SUMMARY")
    print("=" * 60)
    print(f"  Entry price (est.)  : ${price:.2f}")
    print(f"  Initial buy         : ${INITIAL_NOTIONAL} (~{state.total_qty} shares)")
    print()
    print(f"  STOP LOSS (floor)   : ${state.floor:.2f}  (entry × 0.95)")
    print(f"    → sell everything if price drops to ${state.floor:.2f}")
    print()
    print(f"  TRAILING FLOOR")
    print(f"    → activates when price hits   ${state.trail_trigger:.2f}  (+10%)")
    print(f"    → stop moves to current × 0.95, re-raises every +5%")
    print(f"    → floor only moves UP, never down")
    print()
    print(f"  LADDER IN")
    print(f"    → buy ${LADDER_NOTIONAL} more at  ${state.ladder1_price:.2f}  (floor × 0.925)")
    print(f"    → buy ${LADDER_NOTIONAL} more at  ${state.ladder2_price:.2f}  (floor × 0.850)")
    print()
    print(f"  NOTE: Stop loss and ladder logic are software-managed")
    print(f"        (Alpaca doesn't support fractional stop orders)")
    print(f"        Bot must be running for these rules to execute.")
    print("=" * 60)
    print(f"\n  Polling every {POLL_INTERVAL}s. Ctrl+C to stop.\n")

# ── Monitor loop ──────────────────────────────────────────────────────────────

def monitor():
    while not state.stopped_out:
        time.sleep(POLL_INTERVAL)
        try:
            wait_for_market_open()
            price = get_mid_price()
            log(f"price=${price:.2f}  floor=${state.floor:.2f}  "
                f"trail_trigger=${state.trail_trigger:.2f}")

            # Stop loss
            if price <= state.floor:
                log(f"FLOOR HIT at ${price:.2f} (floor=${state.floor:.2f})")
                sell_all("stop loss")
                break

            # Trailing floor
            if price >= state.trail_trigger:
                new_floor = round(price * TRAIL_STOP, 2)
                if new_floor > state.floor:
                    old = state.floor
                    state.floor = new_floor
                    state.trail_trigger = round(price * TRAIL_STEP, 2)
                    log(f"TRAILING: floor raised ${old} → ${state.floor:.2f}, "
                        f"next trigger=${state.trail_trigger:.2f}")

            # Ladder 1
            if not state.ladder1_filled and price <= state.ladder1_price:
                log(f"LADDER 1 triggered at ${price:.2f}")
                buy(LADDER_NOTIONAL, "ladder 1")
                state.ladder1_filled = True

            # Ladder 2
            if not state.ladder2_filled and price <= state.ladder2_price:
                log(f"LADDER 2 triggered at ${price:.2f}")
                buy(LADDER_NOTIONAL, "ladder 2")
                state.ladder2_filled = True

        except Exception as e:
            log(f"ERROR: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    wait_for_market_open()
    setup()
    monitor()
