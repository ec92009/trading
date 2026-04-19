from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from alpaca_env import load_alpaca_credentials

alpaca = load_alpaca_credentials()

client = TradingClient(
    api_key=alpaca["api_key"],
    secret_key=alpaca["secret_key"],
    paper=True,
)

orders = [
    {"symbol": "PLTR",    "notional": 100, "side": OrderSide.SELL, "tif": TimeInForce.DAY},
    {"symbol": "NVDA",    "notional": 50,  "side": OrderSide.BUY,  "tif": TimeInForce.DAY},
    {"symbol": "TSLA",    "notional": 50,  "side": OrderSide.BUY,  "tif": TimeInForce.DAY},
    {"symbol": "BTC/USD", "notional": 50,  "side": OrderSide.BUY,  "tif": TimeInForce.GTC},
]

results = []
for o in orders:
    try:
        order = client.submit_order(MarketOrderRequest(
            symbol=o["symbol"],
            notional=o["notional"],
            side=o["side"],
            time_in_force=o["tif"],
        ))
        results.append((o, order, None))
    except Exception as e:
        results.append((o, None, str(e)))

print("=== QUEUED ORDERS ===")
for o, order, err in results:
    side = "SELL" if o["side"] == OrderSide.SELL else "BUY "
    if order:
        print(f"{side} ${o['notional']:>6} {o['symbol']:<8}  status={order.status}  id={order.id}")
    else:
        print(f"{side} ${o['notional']:>6} {o['symbol']:<8}  ERROR: {err}")

account = client.get_account()
print("\n=== ACCOUNT ===")
print(f"Buying power   : ${float(account.buying_power):,.2f}")
print(f"Portfolio value: ${float(account.portfolio_value):,.2f}")
print(f"Cash           : ${float(account.cash):,.2f}")
