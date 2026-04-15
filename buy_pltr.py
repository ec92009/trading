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

order = client.submit_order(MarketOrderRequest(
    symbol="PLTR",
    qty=1,
    side=OrderSide.BUY,
    time_in_force=TimeInForce.DAY,
))

print("=== ORDER ===")
print(f"ID       : {order.id}")
print(f"Symbol   : {order.symbol}")
print(f"Side     : {order.side}")
print(f"Qty      : {order.qty}")
print(f"Type     : {order.type}")
print(f"Status   : {order.status}")
print(f"Submitted: {order.submitted_at}")

account = client.get_account()
print("\n=== ACCOUNT ===")
print(f"Buying power   : ${float(account.buying_power):,.2f}")
print(f"Portfolio value: ${float(account.portfolio_value):,.2f}")
print(f"Cash           : ${float(account.cash):,.2f}")
