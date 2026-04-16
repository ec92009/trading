"""
Live portfolio bot.

Behavior:
- Maintain a 5-name basket: TSLA, TSM, NVDA, PLTR, BTC/USD
- Flatten unmanaged positions (for example AAPL) when the bot starts
- Watch stock positions during market hours and sell them immediately if they hit
  their beta-scaled stop floor
- Rebalance the basket to equal 20% weights five minutes before the stock market
  close
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoLatestQuoteRequest, StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

import trade_log

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler()],
)


@dataclass
class BotConfig:
    symbol: str
    asset_class: str
    initial_notional: float = 0.0
    ladder_notional: float = 0.0
    stop_pct: float = 0.95
    trail_trigger: float = 1.10
    target_weight: float = 0.20
    base_tol: float = 0.0161
    trail_step: float = 1.0275
    trail_stop: float = 0.995
    ladder1_pct: float = 0.925
    ladder2_pct: float = 0.850
    poll_interval: int = 30


BOTS = [
    BotConfig(symbol="TSLA", asset_class="stock"),
    BotConfig(symbol="TSM", asset_class="stock"),
    BotConfig(symbol="NVDA", asset_class="stock"),
    BotConfig(symbol="PLTR", asset_class="stock"),
    BotConfig(symbol="BTC/USD", asset_class="crypto"),
]

TARGET_SYMBOLS = {cfg.symbol for cfg in BOTS}
ABSORBER_SYMBOL = "BTC/USD"
BETA_WINDOW = 60

_api_key = os.getenv("ALPACA_API_KEY")
_secret_key = os.getenv("ALPACA_SECRET_KEY")

trading = TradingClient(api_key=_api_key, secret_key=_secret_key, paper=True)
stock_data = StockHistoricalDataClient(api_key=_api_key, secret_key=_secret_key)
crypto_data = CryptoHistoricalDataClient(api_key=_api_key, secret_key=_secret_key)


def normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "")


def yf_symbol(symbol: str) -> str:
    return "BTC-USD" if symbol == "BTC/USD" else symbol


def compute_beta(symbol: str) -> float:
    import yfinance as yf

    asset_hist = yf.Ticker(yf_symbol(symbol)).history(period="6mo")["Close"].dropna()
    spy_hist = yf.Ticker("SPY").history(period="6mo")["Close"].dropna()
    common = asset_hist.index.intersection(spy_hist.index)
    if len(common) < 6:
        return 1.0
    asset_close = asset_hist.loc[common].tail(BETA_WINDOW + 1)
    spy_close = spy_hist.loc[common].tail(BETA_WINDOW + 1)
    if len(asset_close) < 6 or len(spy_close) < 6:
        return 1.0
    asset_ret = asset_close.pct_change().dropna()
    spy_ret = spy_close.pct_change().dropna()
    common_ret = asset_ret.index.intersection(spy_ret.index)
    if len(common_ret) < 5:
        return 1.0
    asset_ret = asset_ret.loc[common_ret]
    spy_ret = spy_ret.loc[common_ret]
    am = asset_ret.mean()
    sm = spy_ret.mean()
    cov = ((asset_ret - am) * (spy_ret - sm)).mean()
    var = ((spy_ret - sm) ** 2).mean()
    raw = cov / var if var > 0 else 1.0
    return max(0.3, min(4.0, round(float(raw), 3)))


class Bot:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.logger = logging.getLogger(normalize_symbol(cfg.symbol))
        self.entry_price = 0.0
        self.floor = 0.0
        self.trail_next = 0.0
        self.total_qty = 0.0
        self.qty_precision = 8 if cfg.asset_class == "crypto" else 6
        self.beta = 1.0
        self.beta_asof: date | None = None

    @property
    def tif(self):
        return TimeInForce.GTC if self.cfg.asset_class == "crypto" else TimeInForce.DAY

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

    def refresh_position(self):
        positions = {p.symbol: p for p in trading.get_all_positions()}
        pos = positions.get(normalize_symbol(self.cfg.symbol))
        if not pos:
            self.total_qty = 0.0
            return None
        self.total_qty = float(pos.qty)
        self.entry_price = float(pos.avg_entry_price)
        return pos

    def market_value(self) -> float:
        pos = self.refresh_position()
        return float(pos.market_value) if pos else 0.0

    def ensure_beta(self):
        today = date.today()
        if self.beta_asof == today:
            return
        try:
            self.beta = compute_beta(self.cfg.symbol)
            self.beta_asof = today
        except Exception as exc:
            self.logger.error(f"BETA ERROR: {exc}")
            self.beta = 1.0
            self.beta_asof = today

    def floor_pct(self) -> float:
        self.ensure_beta()
        return max(0.005, self.cfg.base_tol * self.beta)

    def reset_risk_levels(self, anchor_price: float):
        pct = self.floor_pct()
        self.floor = round(anchor_price * (1 - pct), 2)
        self.trail_next = round(anchor_price * (1 + pct), 2)

    def buy(self, notional: float, reason: str):
        if notional < 1.0:
            return
        order = trading.submit_order(
            MarketOrderRequest(
                symbol=self.cfg.symbol,
                notional=round(notional, 2),
                side=OrderSide.BUY,
                time_in_force=self.tif,
            )
        )
        trade_log.log_order(self.cfg.symbol, order.id, "BUY", notional)
        self.logger.info(f"BUY ${notional:.2f} [{reason}] → {order.status} id={order.id}")

    def sell_qty(self, qty: float, reason: str):
        if qty <= 0:
            return
        qty = round(qty, self.qty_precision)
        if qty <= 0:
            return
        try:
            price = self.get_price()
        except Exception:
            price = self.entry_price or 0.0
        order = trading.submit_order(
            MarketOrderRequest(
                symbol=self.cfg.symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=self.tif,
            )
        )
        trade_log.log_order(self.cfg.symbol, order.id, "SELL", qty * price)
        self.logger.info(f"SELL {qty} [{reason}] → {order.status} id={order.id}")

    def sell_all(self, reason: str):
        pos = self.refresh_position()
        if not pos or self.total_qty <= 0:
            self.logger.warning(f"SELL skipped [{reason}] — no live position")
            return
        self.sell_qty(self.total_qty, reason)
        self.floor = 0.0
        self.trail_next = 0.0

    def sync_from_market(self):
        pos = self.refresh_position()
        if not pos:
            return
        if self.floor <= 0 or self.trail_next <= 0:
            try:
                self.reset_risk_levels(self.get_price())
            except Exception as exc:
                self.logger.error(f"SYNC ERROR: {exc}")

    def monitor_stop(self):
        if self.cfg.asset_class != "stock":
            return
        pos = self.refresh_position()
        if not pos or self.total_qty <= 0:
            return
        price = self.get_price()
        self.logger.info(
            f"price=${price:,.2f} floor=${self.floor:,.2f} trail_next=${self.trail_next:,.2f}"
        )
        if self.floor > 0 and price <= self.floor:
            self.logger.info(f"STOP HIT at ${price:,.2f} — exiting position")
            self.sell_all("stop loss")
            return
        if self.trail_next > 0 and price >= self.trail_next:
            new_floor = round(price * self.cfg.trail_stop, 2)
            if new_floor > self.floor:
                old_floor = self.floor
                self.floor = new_floor
                self.trail_next = round(price * self.cfg.trail_step, 2)
                self.logger.info(
                    f"TRAILING: floor ${old_floor:,.2f} → ${self.floor:,.2f}, "
                    f"next trigger=${self.trail_next:,.2f}"
                )


class PortfolioManager:
    def __init__(self, bots: list[Bot]):
        self.bots = bots
        self.bot_by_symbol = {bot.cfg.symbol: bot for bot in bots}
        self.logger = logging.getLogger("portfolio")
        self.last_rebalance_day: date | None = None

    def market_clock(self):
        return trading.get_clock()

    def market_open(self) -> bool:
        return bool(self.market_clock().is_open)

    def now_et(self) -> datetime:
        return self.market_clock().timestamp.replace(tzinfo=timezone.utc).astimezone(
            self.market_clock().timestamp.tzinfo
        )

    def should_rebalance(self, now: datetime, next_close: datetime) -> bool:
        if self.last_rebalance_day == now.date():
            return False
        return next_close - now <= timedelta(minutes=5)

    def account_equity(self) -> float:
        return float(trading.get_account().equity)

    def current_positions(self):
        return {p.symbol: p for p in trading.get_all_positions()}

    def flatten_unmanaged_positions(self):
        positions = self.current_positions()
        for alpaca_symbol, pos in positions.items():
            live_symbol = "BTC/USD" if alpaca_symbol == "BTCUSD" else alpaca_symbol
            if live_symbol in TARGET_SYMBOLS:
                continue
            qty = float(pos.qty)
            price = float(pos.current_price)
            rounded_qty = round(qty, 8 if "/" in live_symbol else 6)
            if qty <= 0 or rounded_qty <= 0 or qty * price < 1.0:
                continue
            asset_class = "crypto" if "USD" in live_symbol and "/" in live_symbol else "stock"
            tif = TimeInForce.GTC if asset_class == "crypto" else TimeInForce.DAY
            order = trading.submit_order(
                MarketOrderRequest(
                    symbol=live_symbol,
                    qty=rounded_qty,
                    side=OrderSide.SELL,
                    time_in_force=tif,
                )
            )
            trade_log.log_order(live_symbol, order.id, "SELL", qty * price)
            self.logger.info(f"Flattened unmanaged position {live_symbol} qty={qty}")

    def rebalance_portfolio(self, reason: str):
        self.logger.info(f"REBALANCE START [{reason}]")
        equity = self.account_equity()
        target_value = round(equity / len(self.bots), 2)
        positions = self.current_positions()

        for bot in self.bots:
            pos = positions.get(normalize_symbol(bot.cfg.symbol))
            current_value = float(pos.market_value) if pos else 0.0
            excess = round(current_value - target_value, 2)
            if excess <= 1.0:
                continue
            price = bot.get_price()
            qty = excess / price
            bot.sell_qty(qty, f"rebalance sell to target ${target_value:,.2f}")

        # Give sell orders a moment to settle into available cash.
        for _ in range(10):
            open_orders = trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
            if not any(str(o.side) == "OrderSide.SELL" for o in open_orders):
                break
            time.sleep(1)

        positions = self.current_positions()
        cash = float(trading.get_account().cash)

        for bot in self.bots:
            pos = positions.get(normalize_symbol(bot.cfg.symbol))
            current_value = float(pos.market_value) if pos else 0.0
            deficit = round(target_value - current_value, 2)
            if deficit <= 1.0 or cash <= 1.0:
                continue
            spend = min(deficit, cash)
            try:
                bot.buy(spend, f"rebalance buy to target ${target_value:,.2f}")
                cash = max(0.0, cash - spend)
            except Exception as exc:
                self.logger.error(f"REBALANCE BUY FAILED {bot.cfg.symbol}: {exc}")

        time.sleep(2)
        for bot in self.bots:
            pos = bot.refresh_position()
            if pos:
                bot.reset_risk_levels(bot.get_price())
        self.last_rebalance_day = self.now_et().date()
        self.logger.info("REBALANCE END")

    def startup_sync(self):
        for bot in self.bots:
            bot.sync_from_market()
        if self.market_open():
            try:
                self.flatten_unmanaged_positions()
            except Exception as exc:
                self.logger.error(f"STARTUP FLATTEN ERROR: {exc}")
            try:
                self.rebalance_portfolio("startup sync")
            except Exception as exc:
                self.logger.error(f"STARTUP REBALANCE ERROR: {exc}")

    def run(self):
        self.startup_sync()
        while True:
            try:
                clock = self.market_clock()
                now = clock.timestamp
                if clock.is_open:
                    for bot in self.bots:
                        bot.monitor_stop()
                    if self.should_rebalance(now, clock.next_close):
                        self.rebalance_portfolio("near close")
                else:
                    self.logger.info(
                        f"Market closed. Next open {clock.next_open.strftime('%Y-%m-%d %H:%M %Z')}"
                    )
                sleep_for = min(cfg.poll_interval for cfg in (bot.cfg for bot in self.bots))
                time.sleep(sleep_for)
            except Exception as exc:
                self.logger.error(f"LOOP ERROR: {exc}")
                time.sleep(30)


if __name__ == "__main__":
    bots = [Bot(cfg) for cfg in BOTS]
    PortfolioManager(bots).run()
