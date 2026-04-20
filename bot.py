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
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoLatestQuoteRequest, StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetCalendarRequest, GetOrdersRequest, MarketOrderRequest

import trade_log
from alpaca_env import load_alpaca_credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
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
VERSION_PATH = Path(__file__).parent / "VERSION"
BOT_FILE_SUFFIX = (os.getenv("BOT_LOG_SUFFIX") or "").strip()
LOG_PATH = Path(__file__).parent / (f"bot_{BOT_FILE_SUFFIX}.log" if BOT_FILE_SUFFIX else "bot.log")
STATE_PATH = Path(__file__).parent / (f"bot_state_{BOT_FILE_SUFFIX}.json" if BOT_FILE_SUFFIX else "bot_state.json")
DECISION_LOG_PATH = Path(__file__).parent / (f"bot_decisions_{BOT_FILE_SUFFIX}.jsonl" if BOT_FILE_SUFFIX else "bot_decisions.jsonl")
LIVE_REBALANCE_ONLY = True

# Crypto holdings still need live risk monitoring even when the equity market
# is closed.
MANAGE_CRYPTO_24X7 = True
_calendar_cache: dict[tuple[date, date], list[date]] = {}
_decision_lock = threading.Lock()

_alpaca = load_alpaca_credentials(os.getenv("ALPACA_PROFILE"))
_api_key = _alpaca["api_key"]
_secret_key = _alpaca["secret_key"]
BOT_VERSION = VERSION_PATH.read_text().strip() if VERSION_PATH.exists() else "0.0"

trading = TradingClient(api_key=_api_key, secret_key=_secret_key, paper=True)
stock_data = StockHistoricalDataClient(api_key=_api_key, secret_key=_secret_key)
crypto_data = CryptoHistoricalDataClient(api_key=_api_key, secret_key=_secret_key)


def normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "")


def yf_symbol(symbol: str) -> str:
    return "BTC-USD" if symbol == "BTC/USD" else symbol


def _decision_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_order_timestamp(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(normalized)
        except ValueError:
            return value
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _order_request_payload(
    *,
    symbol: str,
    side: str,
    time_in_force,
    notional: float | None = None,
    qty: float | None = None,
) -> dict:
    payload = {
        "symbol": symbol,
        "side": side,
        "type": "market",
        "time_in_force": str(time_in_force),
    }
    if notional is not None:
        payload["notional"] = round(notional, 2)
    if qty is not None:
        payload["qty"] = qty
    return payload


def _versioned_rationale(reason: str) -> str:
    return f"BOT v{BOT_VERSION}->{reason}"


def log_decision(
    event_type: str,
    *,
    symbol: str | None = None,
    rationale: str,
    state: dict | None = None,
    order: dict | None = None,
):
    payload = {
        "timestamp_utc": _decision_timestamp(),
        "event_type": event_type,
        "symbol": symbol,
        "rationale": rationale,
        "state": state or {},
        "order": order or {},
    }
    with _decision_lock:
        with DECISION_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _safe_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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

    def buy(
        self,
        notional: float,
        reason: str,
        trade_day: date | None = None,
        decision_context: dict | None = None,
    ):
        if notional < 1.0:
            return None
        reason = _versioned_rationale(reason)
        alpaca_request = _order_request_payload(
            symbol=self.cfg.symbol,
            side="buy",
            notional=round(notional, 2),
            time_in_force=self.tif,
        )
        order = trading.submit_order(
            MarketOrderRequest(
                symbol=self.cfg.symbol,
                notional=round(notional, 2),
                side=OrderSide.BUY,
                time_in_force=self.tif,
            )
        )
        submitted_at = _format_order_timestamp(getattr(order, "submitted_at", None))
        trade_log.log_order(
            self.cfg.symbol,
            order.id,
            "BUY",
            notional,
            alpaca_request=json.dumps(alpaca_request, sort_keys=True),
            rationale=reason,
            submitted_at=submitted_at,
        )
        self.logger.info(
            f"BUY ${notional:.2f} id={order.id} request={alpaca_request} "
            f"submitted_at={submitted_at or '—'} executed_at=— rationale={reason}"
        )
        log_decision(
            "order_submitted",
            symbol=self.cfg.symbol,
            rationale=reason,
            state={
                "side": "BUY",
                "notional": round(notional, 2),
                "trade_day": trade_day.isoformat() if trade_day else None,
                **(decision_context or {}),
            },
            order={
                "id": str(order.id),
                "status": str(order.status),
                "alpaca_request": alpaca_request,
                "submitted_at": submitted_at,
                "executed_at": None,
            },
        )
        if trade_day is not None:
            self.mark_traded(trade_day)
        return order

    def sell_qty(
        self,
        qty: float,
        reason: str,
        trade_day: date | None = None,
        decision_context: dict | None = None,
    ):
        if qty <= 0:
            return None
        reason = _versioned_rationale(reason)
        qty = round(qty, self.qty_precision)
        if qty <= 0:
            return None
        try:
            price = self.get_price()
        except Exception:
            price = self.entry_price or 0.0
        alpaca_request = _order_request_payload(
            symbol=self.cfg.symbol,
            side="sell",
            qty=qty,
            time_in_force=self.tif,
        )
        order = trading.submit_order(
            MarketOrderRequest(
                symbol=self.cfg.symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=self.tif,
            )
        )
        submitted_at = _format_order_timestamp(getattr(order, "submitted_at", None))
        trade_log.log_order(
            self.cfg.symbol,
            order.id,
            "SELL",
            qty * price,
            alpaca_request=json.dumps(alpaca_request, sort_keys=True),
            rationale=reason,
            submitted_at=submitted_at,
        )
        self.logger.info(
            f"SELL {qty} id={order.id} request={alpaca_request} "
            f"submitted_at={submitted_at or '—'} executed_at=— rationale={reason}"
        )
        log_decision(
            "order_submitted",
            symbol=self.cfg.symbol,
            rationale=reason,
            state={
                "side": "SELL",
                "qty": qty,
                "reference_price": round(price, 2),
                "trade_day": trade_day.isoformat() if trade_day else None,
                **(decision_context or {}),
            },
            order={
                "id": str(order.id),
                "status": str(order.status),
                "alpaca_request": alpaca_request,
                "submitted_at": submitted_at,
                "executed_at": None,
            },
        )
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
        if LIVE_REBALANCE_ONLY:
            return None
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
            log_decision(
                "stop_triggered",
                symbol=self.cfg.symbol,
                rationale="Price breached the active stop floor and cooldown allowed a stop sale.",
                state={
                    "price": round(price, 2),
                    "floor": round(self.floor, 2),
                    "sell_qty": sell_qty,
                    "position_qty": round(self.total_qty, self.qty_precision),
                    "stop_sell_pct": self.cfg.stop_sell_pct,
                    "trade_day": trade_day.isoformat(),
                },
            )
            self.sell_qty(
                sell_qty,
                "stop loss",
                trade_day=trade_day,
                decision_context={
                    "trigger_type": "stop",
                    "price": round(price, 2),
                    "floor": round(self.floor, 2),
                    "position_qty": round(self.total_qty, self.qty_precision),
                    "stop_sell_pct": self.cfg.stop_sell_pct,
                },
            )
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
                log_decision(
                    "trail_update",
                    symbol=self.cfg.symbol,
                    rationale="Price crossed the trail trigger, so the stop floor and next trigger were raised.",
                    state={
                        "price": round(price, 2),
                        "old_floor": round(old_floor, 2),
                        "new_floor": round(self.floor, 2),
                        "new_trail_next": round(self.trail_next, 2),
                    },
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

    def lookup_order(self, order_id: str):
        if hasattr(trading, "get_order_by_id"):
            return trading.get_order_by_id(order_id)
        try:
            orders = trading.get_orders(filter=GetOrdersRequest())
        except TypeError:
            orders = trading.get_orders()
        for order in orders:
            if str(getattr(order, "id", "")) == str(order_id):
                return order
        return None

    def canonical_order_status(self, order) -> str:
        status = str(getattr(order, "status", "pending")).lower()
        if status in {"filled", "canceled", "cancelled", "rejected", "expired"}:
            return status
        return "pending"

    def sync_trade_log(self):
        for row in trade_log.all_rows():
            order_id = row.get("order_id")
            if not order_id:
                continue
            current_status = str(row.get("status") or "pending").lower()
            if current_status in {"canceled", "cancelled", "rejected", "expired"}:
                continue
            if current_status == "filled" and (row.get("executed_at") or row.get("filled_at")) and row.get("avg_price"):
                continue
            try:
                order = self.lookup_order(order_id)
            except Exception as exc:
                self.logger.error(f"ORDER SYNC ERROR {order_id}: {exc}")
                continue
            if not order:
                continue
            synced_status = self.canonical_order_status(order)
            filled_avg_price = _safe_float(getattr(order, "filled_avg_price", None))
            if filled_avg_price is None:
                filled_avg_price = _safe_float(getattr(order, "avg_fill_price", None))
            submitted_at = getattr(order, "submitted_at", None)
            filled_at = getattr(order, "filled_at", None)
            needs_update = synced_status != current_status
            needs_update = needs_update or bool(submitted_at and not row.get("submitted_at"))
            if synced_status == "filled":
                needs_update = needs_update or (
                    filled_avg_price is not None and not row.get("avg_price")
                )
                needs_update = needs_update or bool(filled_at and not row.get("executed_at"))
            if not needs_update:
                continue
            trade_log.update_order(
                order_id,
                synced_status,
                avg_price=filled_avg_price if synced_status == "filled" else None,
                submitted_at=submitted_at,
                filled_at=filled_at if synced_status == "filled" else None,
            )
            submitted_at_s = _format_order_timestamp(submitted_at)
            executed_at_s = _format_order_timestamp(filled_at) if synced_status == "filled" else None
            self.logger.info(
                f"ORDER SYNC {row.get('symbol')} {current_status} → {synced_status} "
                f"id={order_id} submitted_at={submitted_at_s or row.get('submitted_at') or '—'} "
                f"executed_at={executed_at_s or row.get('executed_at') or '—'} "
                f"avg_price={filled_avg_price if filled_avg_price is not None else '—'} "
                f"rationale={row.get('rationale') or 'Synchronized local state with Alpaca.'}"
            )
            log_decision(
                "order_status_update",
                symbol=row.get("symbol"),
                rationale=row.get("rationale") or "Synchronized the local trade log with Alpaca order status and fill details.",
                state={
                    "previous_status": current_status,
                    "new_status": synced_status,
                    "logged_submitted_at": row.get("submitted_at") or None,
                    "logged_executed_at": row.get("executed_at") or None,
                    "logged_avg_price": row.get("avg_price") or None,
                },
                order={
                    "id": str(order_id),
                    "status": synced_status,
                    "alpaca_request": row.get("alpaca_request") or None,
                    "submitted_at": submitted_at_s or row.get("submitted_at") or None,
                    "executed_at": executed_at_s or row.get("executed_at") or None,
                    "filled_at": executed_at_s or row.get("filled_at") or None,
                    "avg_price": round(filled_avg_price, 4) if filled_avg_price is not None else None,
                },
            )

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
            alpaca_request = _order_request_payload(
                symbol=live_symbol,
                side="sell",
                qty=rounded_qty,
                time_in_force=tif,
            )
            submitted_at = _format_order_timestamp(getattr(order, "submitted_at", None))
            rationale = _versioned_rationale("startup sync found a live position outside the managed basket and flattened it.")
            trade_log.log_order(
                live_symbol,
                order.id,
                "SELL",
                qty * price,
                alpaca_request=json.dumps(alpaca_request, sort_keys=True),
                rationale=rationale,
                submitted_at=submitted_at,
            )
            self.logger.info(
                f"Flattened unmanaged position {live_symbol} qty={qty} request={alpaca_request} "
                f"submitted_at={submitted_at or '—'} executed_at=— rationale={rationale}"
            )
            log_decision(
                "flatten_unmanaged_position",
                symbol=live_symbol,
                rationale=rationale,
                state={"qty": rounded_qty, "reference_price": round(price, 2)},
                order={
                    "id": str(order.id),
                    "status": str(order.status),
                    "alpaca_request": alpaca_request,
                    "submitted_at": submitted_at,
                    "executed_at": None,
                },
            )

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
            bot.sell_qty(
                qty,
                f"rebalance sell to target ${target_value:,.2f}",
                trade_day=trade_day,
                decision_context={
                    "trigger_type": "rebalance",
                    "target_value": target_value,
                    "current_value": round(current_value, 2),
                    "excess": excess,
                    "equity": round(equity, 2),
                    "rebalance_reason": reason,
                },
            )
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
                bot.buy(
                    spend,
                    f"rebalance buy to target ${target_value:,.2f}",
                    trade_day=trade_day,
                    decision_context={
                        "trigger_type": "rebalance",
                        "target_value": target_value,
                        "current_value": round(current_value, 2),
                        "deficit": deficit,
                        "cash_available": round(cash, 2),
                        "rebalance_reason": reason,
                    },
                )
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
        self.sync_trade_log()
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
        self.sync_trade_log()
        self.save_state()

    def run(self):
        self.startup_sync()
        while True:
            try:
                self.sync_trade_log()
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
                self.sync_trade_log()
                self.save_state()
                sleep_for = min(cfg.poll_interval for cfg in (bot.cfg for bot in self.bots))
                time.sleep(sleep_for)
            except Exception as exc:
                self.logger.error(f"LOOP ERROR: {exc}")
                time.sleep(30)


if __name__ == "__main__":
    bots = [Bot(cfg) for cfg in BOTS]
    PortfolioManager(bots).run()
