import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

load_dotenv()

client = TradingClient(
    api_key=os.getenv("ALPACA_API_KEY"),
    secret_key=os.getenv("ALPACA_SECRET_KEY"),
    paper=True,
)

OLD_ORDER_ID = "63fcdda9-56e5-4c93-b6f1-f7b48498d7e2"

client.cancel_order_by_id(OLD_ORDER_ID)
print(f"Cancelled order {OLD_ORDER_ID}")

order = client.submit_order(MarketOrderRequest(
    symbol="PLTR",
    notional=50,
    side=OrderSide.BUY,
    time_in_force=TimeInForce.DAY,
))

print(f"\n=== NEW ORDER ===")
print(f"ID       : {order.id}")
print(f"Symbol   : {order.symbol}")
print(f"Side     : {order.side}")
print(f"Notional : $50")
print(f"Status   : {order.status}")

account = client.get_account()
print(f"\n=== ACCOUNT ===")
print(f"Buying power   : ${float(account.buying_power):,.2f}")
print(f"Portfolio value: ${float(account.portfolio_value):,.2f}")
print(f"Cash           : ${float(account.cash):,.2f}")
