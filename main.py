from alpaca.trading.client import TradingClient

from alpaca_env import load_alpaca_credentials

alpaca = load_alpaca_credentials()

client = TradingClient(
    api_key=alpaca["api_key"],
    secret_key=alpaca["secret_key"],
    paper=True,
)

account = client.get_account()
print(f"Account status : {account.status}")
print(f"Buying power   : ${float(account.buying_power):,.2f}")
print(f"Portfolio value: ${float(account.portfolio_value):,.2f}")
