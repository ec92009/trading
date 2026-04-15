"""Run only the setup portion of the bot (no monitor loop) to preview the order summary."""
import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from datetime import datetime, timezone

load_dotenv()

SYMBOL           = "AAPL"
INITIAL_NOTIONAL = 50.0
STOP_PCT         = 0.95
TRAIL_TRIGGER    = 1.10
TRAIL_STEP       = 1.05
TRAIL_STOP       = 0.95
LADDER1_PCT      = 0.925
LADDER2_PCT      = 0.85
LADDER_NOTIONAL  = 50.0
POLL_INTERVAL    = 30

trading = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)
data    = StockHistoricalDataClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))

q     = data.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=SYMBOL))[SYMBOL]
price = (float(q.ask_price) + float(q.bid_price)) / 2
qty   = round(INITIAL_NOTIONAL / price, 6)
floor = round(price * STOP_PCT, 2)

order = trading.submit_order(MarketOrderRequest(
    symbol=SYMBOL,
    notional=INITIAL_NOTIONAL,
    side=OrderSide.BUY,
    time_in_force=TimeInForce.DAY,
))

print("\n" + "=" * 60)
print("  AAPL BOT — ORDER & RULES SUMMARY")
print("=" * 60)
print(f"  Entry price (est.)  : ${price:.2f}")
print(f"  Shares (est.)       : {qty}")
print()
print(f"  [ORDER 1] BUY ${INITIAL_NOTIONAL} AAPL @ market")
print(f"            id={order.id}")
print(f"            status={order.status}")
print()
print(f"  [RULE 1] STOP LOSS (floor)")
print(f"           Sell everything if price ≤ ${floor:.2f}  (entry × 0.95)")
print(f"           Managed in software (not a native order)")
print()
print(f"  [RULE 2] TRAILING FLOOR")
print(f"           Activates when price hits ${round(price * TRAIL_TRIGGER, 2):.2f}  (+10%)")
print(f"           → move stop to current × 0.95")
print(f"           → re-raise every +5% after that")
print(f"           → floor only moves UP, never down")
print()
print(f"  [RULE 3] LADDER IN — Level 1")
print(f"           Buy ${LADDER_NOTIONAL} more if price ≤ ${round(floor * LADDER1_PCT, 2):.2f}  (floor × 0.925)")
print()
print(f"  [RULE 4] LADDER IN — Level 2")
print(f"           Buy ${LADDER_NOTIONAL} more if price ≤ ${round(floor * LADDER2_PCT, 2):.2f}  (floor × 0.850)")
print()
print(f"  ⚠  Stop loss and ladder are SOFTWARE-MANAGED.")
print(f"     Run `python3 aapl_bot.py` to activate the monitor.")
print(f"     Bot polls price every {POLL_INTERVAL}s. Keep it running.")
print("=" * 60)
