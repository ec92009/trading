import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

load_dotenv()

client = TradingClient(
    api_key=os.getenv("ALPACA_API_KEY"),
    secret_key=os.getenv("ALPACA_SECRET_KEY"),
    paper=True,
)

positions = client.get_all_positions()
account = client.get_account()

print("=== POSITIONS ===")
if positions:
    for p in positions:
        pl = float(p.unrealized_pl)
        pl_pct = float(p.unrealized_plpc) * 100
        sign = "+" if pl >= 0 else ""
        print(f"{p.symbol:<10} qty={p.qty:<8} avg=${float(p.avg_entry_price):<10.2f} "
              f"mkt=${float(p.market_value):<10.2f} P&L={sign}{pl:.2f} ({sign}{pl_pct:.2f}%)")
else:
    print("No open positions.")

orders = client.get_orders()
print(f"\n=== PENDING ORDERS ({len(orders)}) ===")
for o in orders:
    side = "BUY " if str(o.side) == "OrderSide.BUY" else "SELL"
    amt = f"${float(o.notional):.0f}" if o.notional else f"{o.qty} sh"
    print(f"{side} {amt:<8} {o.symbol:<10} status={o.status}")

print(f"\n=== ACCOUNT ===")
print(f"Cash           : ${float(account.cash):,.2f}")
print(f"Buying power   : ${float(account.buying_power):,.2f}")
print(f"Portfolio value: ${float(account.portfolio_value):,.2f}")
print(f"P&L today      : ${float(account.equity) - float(account.last_equity):,.2f}")
