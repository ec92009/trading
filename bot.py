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
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest
import trade_log

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

_P = 348.71   # portfolio value at last rebalance — update if you rebalance again
BOTS = [
    BotConfig(symbol="AAPL",    asset_class="stock",  initial_notional=round(_P*0.10, 2), ladder_notional=round(_P*0.10, 2)),
    BotConfig(symbol="BTC/USD", asset_class="crypto", initial_notional=round(_P*0.10, 2), ladder_notional=round(_P*0.10, 2)),
    BotConfig(symbol="PLTR",    asset_class="stock",  initial_notional=round(_P*0.20, 2), ladder_notional=round(_P*0.20, 2)),
    BotConfig(symbol="TSM",     asset_class="stock",  initial_notional=round(_P*0.20, 2), ladder_notional=round(_P*0.20, 2)),
    BotConfig(symbol="NVDA",    asset_class="stock",  initial_notional=round(_P*0.20, 2), ladder_notional=round(_P*0.20, 2)),
    BotConfig(symbol="TSLA",    asset_class="stock",  initial_notional=round(_P*0.20, 2), ladder_notional=round(_P*0.20, 2)),
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
        trade_log.log_order(self.cfg.symbol, order.id, "BUY", notional)
        self.logger.info(f"BUY ${notional} [{reason}] → {order.status} id={order.id}")

    def sell_all(self, reason: str):
        order = trading.submit_order(MarketOrderRequest(
            symbol=self.cfg.symbol,
            qty=round(self.total_qty, self.qty_precision),
            side=OrderSide.SELL,
            time_in_force=self.tif,
        ))
        trade_log.log_order(self.cfg.symbol, order.id, "SELL", self.total_qty * self.entry_price)
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

    def _alpaca_order_status(self, order_id: str) -> str:
        """Return Alpaca's current status string for an order, or 'unknown'."""
        try:
            o = trading.get_order_by_id(order_id)
            return str(o.status)   # e.g. 'OrderStatus.FILLED'
        except Exception:
            return "unknown"

    def _cancel_open_buys_except(self, keep_id: str | None = None):
        """Cancel all open buy orders for this symbol, optionally sparing one."""
        orders = trading.get_orders(GetOrdersRequest(
            symbols=[self.cfg.symbol],        # must be a list — symbol= is ignored by SDK
            status=QueryOrderStatus.OPEN,
        ))
        for o in orders:
            if str(o.side) == "OrderSide.BUY" and str(o.id) != keep_id:
                trading.cancel_order_by_id(o.id)
                trade_log.update_order(str(o.id), "cancelled")
                self.logger.info(f"Cancelled duplicate buy order {o.id}")

    def setup(self):
        self.wait_for_market()
        cfg = self.cfg

        # ── 1. Existing filled position → resume monitoring ───────────────────
        positions = {p.symbol: p for p in trading.get_all_positions()}
        existing  = positions.get(cfg.symbol.replace("/", ""))

        if existing:
            self.entry_price = float(existing.avg_entry_price)
            self.total_qty   = float(existing.qty)
            self._cancel_open_buys_except(keep_id=None)
            self.logger.info(
                f"Resuming existing position: "
                f"{self.total_qty} @ ${self.entry_price:,.2f}"
            )

        else:
            # ── 2. Check TSV for a pending buy we already queued ──────────────
            tsv_row = trade_log.get_pending_buy(cfg.symbol)

            if tsv_row:
                alpaca_status = self._alpaca_order_status(tsv_row["order_id"])

                if "FILLED" in alpaca_status.upper():
                    # Filled since last run — update TSV, use fill price
                    o = trading.get_order_by_id(tsv_row["order_id"])
                    fill_price = float(o.filled_avg_price or self.get_price())
                    trade_log.update_order(tsv_row["order_id"], "filled", fill_price)
                    self.entry_price = fill_price
                    self.total_qty   = float(o.filled_qty or 0)
                    self._cancel_open_buys_except(keep_id=None)
                    self.logger.info(
                        f"TSV buy filled @ ${fill_price:,.2f} — resuming"
                    )

                elif "CANCELLED" in alpaca_status.upper() or alpaca_status == "unknown":
                    # Order gone — mark TSV cancelled, place fresh buy
                    trade_log.update_order(tsv_row["order_id"], "cancelled")
                    self.logger.info(
                        f"TSV buy {tsv_row['order_id']} was cancelled — placing fresh order"
                    )
                    price            = self.get_price()
                    self.entry_price = price
                    self.total_qty   = round(cfg.initial_notional / price, self.qty_precision)
                    self.buy(cfg.initial_notional, "initial entry")

                else:
                    # Still pending on Alpaca — keep it, don't re-queue
                    self._cancel_open_buys_except(keep_id=tsv_row["order_id"])
                    price            = self.get_price()
                    self.entry_price = price   # estimate until fill
                    self.total_qty   = round(cfg.initial_notional / price, self.qty_precision)
                    self.logger.info(
                        f"TSV buy {tsv_row['order_id']} still pending — "
                        f"resuming with estimated entry ${price:,.2f}"
                    )

            else:
                # ── 3. No TSV record — fresh entry ────────────────────────────
                self._cancel_open_buys_except(keep_id=None)
                price            = self.get_price()
                self.entry_price = price
                self.total_qty   = round(cfg.initial_notional / price, self.qty_precision)
                self.buy(cfg.initial_notional, "initial entry")

        self.floor         = round(self.entry_price * cfg.stop_pct,      2)
        self.ladder1_price = round(self.floor * cfg.ladder1_pct,         2)
        self.ladder2_price = round(self.floor * cfg.ladder2_pct,         2)
        self.trail_next    = round(self.entry_price * cfg.trail_trigger,  2)

        fmt = lambda n: f"${n:,.2f}"
        self.logger.info("=" * 50)
        self.logger.info(f"  {cfg.symbol} BOT STARTED")
        self.logger.info(f"  Entry          : {fmt(self.entry_price)}")
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
