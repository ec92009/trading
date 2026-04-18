"""
Live portfolio bot.

Behavior:
- Maintain a 5-name basket: TSLA, TSM, NVDA, PLTR, BTC/USD
- Flatten unmanaged positions (for example AAPL) when the bot starts
- Watch stock positions during market hours and apply beta-scaled stops/trails
- Keep stop-sale and rebalance-sale proceeds in cash
- Rebalance the basket to target weights five minutes before the stock market
  close: TSLA 50%, TSM/NVDA/PLTR/BTC 12.5% each
"""

from __future__ import annotations

import json
import logging
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
from alpaca.trading.requests import GetCalendarRequest, GetOrdersRequest, MarketOrderRequest

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
    target_weight: float = 0.20
    base_tol: float = 0.0109
    trail_step: float = 1.0235
    trail_stop: float = 0.9885
    stop_sell_pct: float = 0.8342
    stop_cooldown_days: int = 5
    poll_interval: int = 30


BOTS = [
    BotConfig(symbol="TSLA", asset_class="stock", target_weight=0.50),
    BotConfig(symbol="TSM", asset_class="stock", target_weight=0.125),
    BotConfig(symbol="NVDA", asset_class="stock", target_weight=0.125),
    BotConfig(symbol="PLTR", asset_class="stock", target_weight=0.125),
    BotConfig(symbol="BTC/USD", asset_class="crypto", target_weight=0.125),
]

TARGET_SYMBOLS = {cfg.symbol for cfg in BOTS}
BETA_WINDOW = 60
STATE_PATH = Path(__file__).parent / "bot_state.json"

# Crypto holdings still need live risk monitoring even when the equity market
# is closed.
MANAGE_CRYPTO_24X7 = True
_calendar_cache: dict[tuple[date, date], list[date]] = {}

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


def _weekday_add(start_day: date, trading_days: int) -> date:
    result = start_day
    remaining = max(0, trading_days)
    while remaining > 0:
        result += timedelta(days=1)
        if result.weekday() < 5:
            remaining -= 1
    return result


def add_trading_days(start_day: date, trading_days: int) -> date:
    trading_days = max(0, trading_days)
    if trading_days == 0:
        return start_day
    # Use Alpaca's market calendar so holidays and early-close days follow the
    # actual equity session schedule. Fall back to weekdays if the calendar is
    # unavailable so the bot can keep running.
    window_end = start_day + timedelta(days=max(14, trading_days * 5))
    cache_key = (start_day, window_end)
    try:
        if cache_key not in _calendar_cache:
            sessions = trading.get_calendar(
                GetCalendarRequest(start=start_day, end=window_end)
            )
            _calendar_cache[cache_key] = [session.date for session in sessions if session.date > start_day]
        sessions = _calendar_cache[cache_key]
        if len(sessions) >= trading_days:
            return sessions[trading_days - 1]
    except Exception:
        pass
    return _weekday_add(start_day, trading_days)


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
        self.stop_ready_on: date | None = None
        self.last_trade_on: date | None = None

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

    def export_state(self) -> dict:
        return {
            "floor": self.floor,
            "trail_next": self.trail_next,
            "stop_ready_on": self.stop_ready_on.isoformat() if self.stop_ready_on else None,
            "last_trade_on": self.last_trade_on.isoformat() if self.last_trade_on else None,
        }

    def load_state(self, state: dict | None):
        if not state:
            return
        self.floor = float(state.get("floor") or 0.0)
        self.trail_next = float(state.get("trail_next") or 0.0)
        stop_ready_on = state.get("stop_ready_on")
        last_trade_on = state.get("last_trade_on")
        self.stop_ready_on = date.fromisoformat(stop_ready_on) if stop_ready_on else None
        self.last_trade_on = date.fromisoformat(last_trade_on) if last_trade_on else None

    def traded_on(self, trade_day: date) -> bool:
        return self.last_trade_on == trade_day

    def mark_traded(self, trade_day: date):
        self.last_trade_on = trade_day

    def stop_ready(self, trade_day: date) -> bool:
        return self.stop_ready_on is None or trade_day >= self.stop_ready_on

    def set_stop_cooldown(self, trade_day: date):
        cooldown = max(0, int(self.cfg.stop_cooldown_days))
        self.stop_ready_on = add_trading_days(trade_day, cooldown + 1)

    def estimate_qty(self, notional: float) -> float:
        price = self.get_price()
        if price <= 0:
            return 0.0
        return round(notional / price, self.qty_precision)

    def buy(self, notional: float, reason: str, trade_day: date | None = None):
        if notional < 1.0:
            return None
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
        if trade_day is not None:
            self.mark_traded(trade_day)
        return order

    def sell_qty(self, qty: float, reason: str, trade_day: date | None = None):
        if qty <= 0:
            return None
        qty = round(qty, self.qty_precision)
        if qty <= 0:
            return None
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
        if trade_day is not None:
            self.mark_traded(trade_day)
        return order

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

    def monitor_risk(self, trade_day: date):
        pos = self.refresh_position()
        if not pos or self.total_qty <= 0:
            return None
        price = self.get_price()
        self.logger.info(
            f"price=${price:,.2f} floor=${self.floor:,.2f} trail_next=${self.trail_next:,.2f}"
        )
        if self.floor > 0 and price <= self.floor and self.stop_ready(trade_day):
            sell_qty = round(self.total_qty * self.cfg.stop_sell_pct, self.qty_precision)
            sell_qty = min(sell_qty, round(self.total_qty, self.qty_precision))
            if sell_qty <= 0:
                return None
            stop_price = self.floor
            self.logger.info(
                f"STOP HIT at ${price:,.2f} — selling {self.cfg.stop_sell_pct:.0%} and parking proceeds"
            )
            self.sell_qty(sell_qty, "stop loss", trade_day=trade_day)
            self.reset_risk_levels(stop_price)
            self.set_stop_cooldown(trade_day)
            return {
                "action": "stop",
                "symbol": self.cfg.symbol,
                "qty": sell_qty,
                "price": stop_price,
                "proceeds": round(sell_qty * stop_price, 2),
            }
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
        return None


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

    def state_payload(self) -> dict:
        return {
            "last_rebalance_day": self.last_rebalance_day.isoformat() if self.last_rebalance_day else None,
            "bots": {bot.cfg.symbol: bot.export_state() for bot in self.bots},
        }

    def save_state(self):
        STATE_PATH.write_text(json.dumps(self.state_payload(), indent=2, sort_keys=True))

    def load_state(self):
        if not STATE_PATH.exists():
            return
        try:
            payload = json.loads(STATE_PATH.read_text())
        except Exception as exc:
            self.logger.error(f"STATE LOAD ERROR: {exc}")
            return
        last_rebalance_day = payload.get("last_rebalance_day")
        self.last_rebalance_day = date.fromisoformat(last_rebalance_day) if last_rebalance_day else None
        bot_states = payload.get("bots") or {}
        for bot in self.bots:
            bot.load_state(bot_states.get(bot.cfg.symbol))

    def can_trade(self, bot: Bot, trade_day: date) -> bool:
        return not bot.traded_on(trade_day)

    def settle_sell_orders(self):
        for _ in range(10):
            open_orders = trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
            if not any(o.side == OrderSide.SELL for o in open_orders):
                break
            time.sleep(1)

    def should_monitor_bot(self, bot: Bot, market_is_open: bool) -> bool:
        if bot.cfg.asset_class == "stock":
            return market_is_open
        return MANAGE_CRYPTO_24X7

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
        trade_day = self.now_et().date()
        equity = self.account_equity()
        total_weight = sum(max(0.0, bot.cfg.target_weight) for bot in self.bots)
        if total_weight <= 0:
            total_weight = len(self.bots)
        target_value_by_symbol = {
            bot.cfg.symbol: round(
                equity * (max(0.0, bot.cfg.target_weight) / total_weight if total_weight else 0.0),
                2,
            )
            for bot in self.bots
        }

        for bot in self.bots:
            target_value = target_value_by_symbol[bot.cfg.symbol]
            bot.refresh_position()
            price = bot.get_price()
            current_value = bot.total_qty * price
            excess = round(current_value - target_value, 2)
            if excess <= 1.0 or not self.can_trade(bot, trade_day):
                continue
            qty = round(excess / price, bot.qty_precision)
            if qty <= 0:
                continue
            bot.sell_qty(qty, f"rebalance sell to target ${target_value:,.2f}", trade_day=trade_day)
            self.logger.info(
                f"CASH BUFFER +${round(qty * price, 2):,.2f} [rebalance sell {bot.cfg.symbol}]"
            )

        self.settle_sell_orders()

        deficits: list[tuple[float, Bot]] = []
        for bot in self.bots:
            target_value = target_value_by_symbol[bot.cfg.symbol]
            bot.refresh_position()
            price = bot.get_price()
            current_value = bot.total_qty * price
            deficit = round(target_value - current_value, 2)
            if deficit > 1.0:
                deficits.append((deficit, bot))
        deficits.sort(key=lambda item: item[0], reverse=True)

        for deficit, bot in deficits:
            target_value = target_value_by_symbol[bot.cfg.symbol]
            if not self.can_trade(bot, trade_day):
                continue
            cash = float(trading.get_account().cash)
            spend = min(deficit, cash)
            if spend <= 1.0:
                continue
            try:
                bot.buy(spend, f"rebalance buy to target ${target_value:,.2f}", trade_day=trade_day)
            except Exception as exc:
                self.logger.error(f"REBALANCE BUY FAILED {bot.cfg.symbol}: {exc}")

        time.sleep(2)
        for bot in self.bots:
            pos = bot.refresh_position()
            if pos:
                bot.reset_risk_levels(bot.get_price())
        self.last_rebalance_day = trade_day
        self.save_state()
        self.logger.info("REBALANCE END")

    def startup_sync(self):
        self.load_state()
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
        self.save_state()

    def run(self):
        self.startup_sync()
        while True:
            try:
                clock = self.market_clock()
                now = clock.timestamp
                monitored_any = False
                for bot in self.bots:
                    if not self.should_monitor_bot(bot, clock.is_open):
                        continue
                    monitored_any = True
                    event = bot.monitor_risk(now.date())
                    if event and event["action"] == "stop":
                        self.logger.info(
                            f"CASH BUFFER +${event['proceeds']:,.2f} [stop sell {event['symbol']}]"
                        )
                if monitored_any and self.should_rebalance(now, clock.next_close):
                        self.rebalance_portfolio("near close")
                if not clock.is_open:
                    self.logger.info(
                        f"Market closed. Next open {clock.next_open.strftime('%Y-%m-%d %H:%M %Z')}"
                    )
                self.save_state()
                sleep_for = min(cfg.poll_interval for cfg in (bot.cfg for bot in self.bots))
                time.sleep(sleep_for)
            except Exception as exc:
                self.logger.error(f"LOOP ERROR: {exc}")
                time.sleep(30)


if __name__ == "__main__":
    bots = [Bot(cfg) for cfg in BOTS]
    PortfolioManager(bots).run()
