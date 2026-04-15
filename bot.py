"""
Generic trading bot — runs multiple symbols concurrently in threads.
Each symbol gets its own BotConfig with independent state and rules.

Rules (same for all symbols):
- Buy initial notional at market on startup
- Stop loss: sell all if price drops to entry * 0.95
- Trailing floor: after +10%, raise stop to current * 0.95; re-raise every +5%
- Ladder in: buy more at floor * 0.925, buy more at floor * 0.850
- Stocks: market hours only (DAY orders). Crypto: 24/7 (GTC orders).
"""

import os
import time
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest

load_dotenv(Path(__file__).parent / ".env")

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler()],
)

# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class BotConfig:
    symbol:           str
    asset_class:      str    # "stock" or "crypto"
    initial_notional: float  = 50.0
    ladder_notional:  float  = 50.0
    stop_pct:         float  = 0.95
    trail_trigger:    float  = 1.10
    trail_step:       float  = 1.05
    trail_stop:       float  = 0.95
    ladder1_pct:      float  = 0.925
    ladder2_pct:      float  = 0.850
    poll_interval:    int    = 30

BOTS = [
    BotConfig(symbol="AAPL",    asset_class="stock"),
    BotConfig(symbol="BTC/USD", asset_class="crypto"),
]

# ── Shared clients ────────────────────────────────────────────────────────────

_api_key    = os.getenv("ALPACA_API_KEY")
_secret_key = os.getenv("ALPACA_SECRET_KEY")

trading      = TradingClient(api_key=_api_key, secret_key=_secret_key, paper=True)
stock_data   = StockHistoricalDataClient(api_key=_api_key, secret_key=_secret_key)
crypto_data  = CryptoHistoricalDataClient(api_key=_api_key, secret_key=_secret_key)

# ── Bot ───────────────────────────────────────────────────────────────────────

class Bot:
    def __init__(self, cfg: BotConfig):
        self.cfg          = cfg
        self.logger       = logging.getLogger(cfg.symbol.replace("/", ""))
        self.entry_price  = 0.0
        self.floor        = 0.0
        self.ladder1_price = 0.0
        self.ladder2_price = 0.0
        self.trail_next   = 0.0
        self.total_qty    = 0.0
        self.ladder1_done = False
        self.ladder2_done = False
        self.stopped_out  = False
        self.qty_precision = 8 if cfg.asset_class == "crypto" else 6

    # ── Price ─────────────────────────────────────────────────────────────────

    def get_price(self) -> float:
        if self.cfg.asset_class == "crypto":
            q = crypto_data.get_crypto_latest_quote(
                CryptoLatestQuoteRequest(symbol_or_symbols=self.cfg.symbol)
            )[self.cfg.symbol]
        else:
            q = stock_data.get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=self.cfg.symbol)
            )[self.cfg.symbol]
        ask = float(q.ask_price or 0)
        bid = float(q.bid_price or 0)
        return (ask + bid) / 2 if (ask and bid) else ask or bid

    # ── Orders ────────────────────────────────────────────────────────────────

    @property
    def tif(self):
        return TimeInForce.GTC if self.cfg.asset_class == "crypto" else TimeInForce.DAY

    def check_buying_power(self, needed: float):
        """Raise if account buying power is insufficient."""
        bp = float(trading.get_account().buying_power)
        if bp < needed:
            raise RuntimeError(
                f"Insufficient buying power: need ${needed}, available ${bp:.2f}"
            )

    def buy(self, notional: float, reason: str):
        self.check_buying_power(notional)
        order = trading.submit_order(MarketOrderRequest(
            symbol=self.cfg.symbol,
            notional=notional,
            side=OrderSide.BUY,
            time_in_force=self.tif,
        ))
        self.total_qty += round(notional / self.entry_price, self.qty_precision)
        self.logger.info(f"BUY ${notional} [{reason}] → {order.status} id={order.id}")

    def sell_all(self, reason: str):
        order = trading.submit_order(MarketOrderRequest(
            symbol=self.cfg.symbol,
            qty=round(self.total_qty, self.qty_precision),
            side=OrderSide.SELL,
            time_in_force=self.tif,
        ))
        self.stopped_out = True
        self.logger.info(f"SELL ALL {self.total_qty} [{reason}] → {order.status} id={order.id}")

    # ── Market hours guard (stocks only) ──────────────────────────────────────

    def wait_for_market(self):
        if self.cfg.asset_class == "crypto":
            return
        clock = trading.get_clock()
        if clock.is_open:
            return
        now       = datetime.now(timezone.utc)
        next_open = clock.next_open.replace(tzinfo=timezone.utc)
        secs      = max(0, (next_open - now).total_seconds())
        self.logger.info(
            f"Market closed. Sleeping {secs/3600:.1f}h until "
            f"{clock.next_open.strftime('%Y-%m-%d %H:%M %Z')}"
        )
        time.sleep(secs + 5)

    # ── Setup ─────────────────────────────────────────────────────────────────

    def setup(self):
        self.wait_for_market()
        price = self.get_price()
        cfg   = self.cfg

        self.entry_price   = price
        self.floor         = round(price * cfg.stop_pct,     2)
        self.ladder1_price = round(self.floor * cfg.ladder1_pct, 2)
        self.ladder2_price = round(self.floor * cfg.ladder2_pct, 2)
        self.trail_next    = round(price * cfg.trail_trigger, 2)
        self.total_qty     = round(cfg.initial_notional / price, self.qty_precision)

        self.buy(cfg.initial_notional, "initial entry")

        fmt = lambda n: f"${n:,.2f}"
        self.logger.info("=" * 50)
        self.logger.info(f"  {cfg.symbol} BOT STARTED")
        self.logger.info(f"  Entry          : {fmt(price)}")
        self.logger.info(f"  Stop loss      : {fmt(self.floor)}  (×0.95)")
        self.logger.info(f"  Trail trigger  : {fmt(self.trail_next)}  (+10%)")
        self.logger.info(f"  Ladder 1       : {fmt(self.ladder1_price)}  (floor×0.925)")
        self.logger.info(f"  Ladder 2       : {fmt(self.ladder2_price)}  (floor×0.850)")
        self.logger.info("=" * 50)

    # ── Monitor ───────────────────────────────────────────────────────────────

    def monitor(self):
        while not self.stopped_out:
            time.sleep(self.cfg.poll_interval)
            try:
                self.wait_for_market()
                price = self.get_price()
                self.logger.info(
                    f"price=${price:,.2f}  floor=${self.floor:,.2f}  "
                    f"trail_next=${self.trail_next:,.2f}"
                )

                if price <= self.floor:
                    self.logger.info(f"FLOOR HIT at ${price:,.2f}")
                    self.sell_all("stop loss")
                    break

                if price >= self.trail_next:
                    new_floor = round(price * self.cfg.trail_stop, 2)
                    if new_floor > self.floor:
                        old = self.floor
                        self.floor      = new_floor
                        self.trail_next = round(price * self.cfg.trail_step, 2)
                        self.logger.info(
                            f"TRAILING: floor ${old:,.2f} → ${self.floor:,.2f}, "
                            f"next trigger=${self.trail_next:,.2f}"
                        )

                if not self.ladder1_done and price <= self.ladder1_price:
                    self.logger.info(f"LADDER 1 at ${price:,.2f}")
                    self.buy(self.cfg.ladder_notional, "ladder 1")
                    self.ladder1_done = True

                if not self.ladder2_done and price <= self.ladder2_price:
                    self.logger.info(f"LADDER 2 at ${price:,.2f}")
                    self.buy(self.cfg.ladder_notional, "ladder 2")
                    self.ladder2_done = True

            except Exception as e:
                self.logger.error(f"ERROR: {e}")

    def run(self):
        retry_interval = 60
        while True:
            try:
                self.setup()
                break
            except RuntimeError as e:
                self.logger.error(f"SETUP FAILED: {e} — retrying in {retry_interval}s")
                time.sleep(retry_interval)
            except Exception as e:
                self.logger.error(f"SETUP ERROR: {e} — retrying in {retry_interval}s")
                time.sleep(retry_interval)
        self.monitor()

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    threads = [threading.Thread(target=Bot(cfg).run, daemon=True) for cfg in BOTS]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
