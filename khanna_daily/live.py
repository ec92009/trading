from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import bot as basket_bot
import copytrade_demo as demo
import remote_snapshots
import trade_log
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

from . import market_data
from . import signal_updater

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)

HERE = Path(__file__).resolve().parent.parent
SIGNALS_PATH = HERE / "copytrade_signals.json"
STATE_PATH = HERE / (
    f"copytrade_state_{(os.getenv('BOT_LOG_SUFFIX') or '').strip()}.json"
    if (os.getenv("BOT_LOG_SUFFIX") or "").strip()
    else "copytrade_state.json"
)

POLITICIAN = "Ro Khanna"
MIN_BAND = "< 1K"
ENTRY_LAG_TRADING_DAYS = 1
HALF_LIFE_DAYS = 60.0
DAILY_DECAY_PCT = 1.0 - (0.5 ** (1.0 / HALF_LIFE_DAYS))
MAX_NAMES = 10
SIM_CAPITAL = 10000.0
POLL_INTERVAL = 30
SIGNAL_REFRESH_INTERVAL = 900
IGNORED_SYMBOLS = {"SPX"}
MAX_COMPLETION_ATTEMPTS_PER_ASSET = 5
LIVE_POINT_SYSTEM = {
    "< 1K": 0.125,
    "1K-15K": 0.25,
    "15K-50K": 0.5,
    "50K-100K": 1.0,
    "100K-250K": 1.0,
    "250K-500K": 2.0,
    "500K-1M": 4.0,
    "1M-5M": 10.0,
    "5M-25M": 20.0,
}


@contextmanager
def _live_point_system():
    original = demo.BAND_POINTS.copy()
    demo.BAND_POINTS = {**demo.BAND_POINTS, **LIVE_POINT_SYSTEM}
    try:
        yield
    finally:
        demo.BAND_POINTS = original


def _normalize_live_symbol(symbol: str) -> str:
    return "BTC/USD" if symbol == "BTCUSD" else symbol


def _weights_from_simulation(result: dict) -> dict[str, float]:
    positions = result.get("positions") or {}
    return {
        symbol: round(float(position.get("weight") or 0.0), 4)
        for symbol, position in positions.items()
        if float(position.get("weight") or 0.0) > 0
    }


def _signature_for(result: dict, weights: dict[str, float]) -> str:
    last_trade_day = ((result.get("trade_window") or {}).get("last_trade_day")) or "none"
    ordered_weights = ",".join(f"{symbol}:{weight:.4f}" for symbol, weight in sorted(weights.items()))
    return f"{last_trade_day}|{ordered_weights}"


class CopyTradeLiveManager:
    def __init__(self):
        self.logger = logging.getLogger("copytrade")
        self.order_sync = basket_bot.PortfolioManager([])
        self.last_rebalance_signature: str | None = None
        self._cached_simulation_key: tuple[str, float] | None = None
        self._cached_simulation_result: dict | None = None
        self._last_signal_refresh_at = 0.0
        self.snapshot_publisher = remote_snapshots.RemoteSnapshotPublisher(
            bot_log_path=basket_bot.LOG_PATH,
            decision_log_path=basket_bot.DECISION_LOG_PATH,
            trade_log_path=trade_log.LOG_PATH,
            bundle_name="copybot",
            portfolio_snapshot_provider=self.build_portfolio_snapshot,
            logger=self.logger,
        )

    def market_clock(self):
        return basket_bot.trading.get_clock()

    def market_open(self) -> bool:
        return bool(self.market_clock().is_open)

    def now_et(self) -> datetime:
        return self.market_clock().timestamp.astimezone(demo.ET)

    def _signal_mtime(self) -> float:
        return SIGNALS_PATH.stat().st_mtime if SIGNALS_PATH.exists() else 0.0

    def refresh_signals_if_due(self, *, force: bool = False):
        now = time.time()
        if not force and now - self._last_signal_refresh_at < SIGNAL_REFRESH_INTERVAL:
            return
        try:
            result = signal_updater.refresh_politician_signals()
            self._last_signal_refresh_at = now
            if result["added"]:
                self.logger.info(
                    "Refreshed Capitol signals for %s: +%s trades across %s page(s).",
                    POLITICIAN,
                    result["added"],
                    result["pages_scanned"],
                )
            else:
                self.logger.info(
                    "Checked Capitol signals for %s: no new trades across %s page(s).",
                    POLITICIAN,
                    result["pages_scanned"],
                )
        except Exception as exc:
            self.logger.error("SIGNAL REFRESH ERROR: %s", exc)

    def load_state(self):
        if not STATE_PATH.exists():
            return
        try:
            payload = json.loads(STATE_PATH.read_text())
        except Exception as exc:
            self.logger.error(f"STATE LOAD ERROR: {exc}")
            return
        signature = payload.get("last_rebalance_signature")
        self.last_rebalance_signature = str(signature) if signature else None

    def save_state(self):
        STATE_PATH.write_text(json.dumps({"last_rebalance_signature": self.last_rebalance_signature}, indent=2, sort_keys=True))

    def simulate_target_book(self, as_of: str) -> dict:
        cache_key = (as_of, self._signal_mtime())
        if self._cached_simulation_key == cache_key and self._cached_simulation_result is not None:
            return self._cached_simulation_result
        signals = demo.load_signals(SIGNALS_PATH, politician=POLITICIAN)
        with _live_point_system():
            eligible = [
                signal
                for signal in signals
                if demo.qualifies(signal, MIN_BAND) and demo.target_points(signal) > 0
            ]
            if eligible:
                start = min(signal.published_at for signal in eligible)
                symbols = sorted({signal.symbol for signal in eligible})
                trading_days, market, skipped_symbols = market_data.load_market_series(
                    symbols,
                    start=start,
                    end=as_of,
                    ignored_symbols=IGNORED_SYMBOLS,
                )
                result = demo.simulate_with_market(
                    signals,
                    market=market,
                    trading_days=trading_days,
                    capital=SIM_CAPITAL,
                    min_band=MIN_BAND,
                    max_names=MAX_NAMES,
                    entry_lag_trading_days=ENTRY_LAG_TRADING_DAYS,
                    daily_decay_pct=DAILY_DECAY_PCT,
                    end=as_of,
                    skipped_symbols=skipped_symbols,
                )
            else:
                result = {
                    "trade_window": {"last_trade_day": as_of},
                    "active_queue": [],
                    "positions": {},
                    "skipped_symbols": {},
                }
        result["point_system"] = dict(LIVE_POINT_SYSTEM)
        self._cached_simulation_key = cache_key
        self._cached_simulation_result = result
        return result

    def current_positions(self) -> dict[str, object]:
        return {_normalize_live_symbol(position.symbol): position for position in basket_bot.trading.get_all_positions()}

    def _target_value_by_symbol(self, target_weights: dict[str, float]) -> tuple[float, dict[str, float]]:
        equity = float(basket_bot.trading.get_account().equity)
        return equity, {symbol: round(equity * weight, 2) for symbol, weight in target_weights.items()}

    def build_portfolio_snapshot(self) -> dict:
        as_of = self.now_et().isoformat()
        result = self.simulate_target_book(self.now_et().date().isoformat())
        target_weights = _weights_from_simulation(result)
        account = basket_bot.trading.get_account()
        equity = float(account.equity)
        cash = float(account.cash)
        positions = self.current_positions()
        rows = []
        allocated_total = 0.0
        _, target_value_by_symbol = self._target_value_by_symbol(target_weights)
        current_points = result.get("current_points") or {}
        for symbol in sorted(set(target_weights) | set(positions)):
            position = positions.get(symbol)
            current_value = float(getattr(position, "market_value", 0.0) or 0.0)
            qty = float(getattr(position, "qty", 0.0) or 0.0) if position else 0.0
            current_weight = (current_value / equity) if equity > 0 else 0.0
            allocated_total += current_value
            rows.append(
                {
                    "symbol": symbol,
                    "target_weight": round(target_weights.get(symbol, 0.0), 6),
                    "current_weight": round(current_weight, 6),
                    "points": round(float(current_points.get(symbol, 0.0) or 0.0), 4),
                    "current_value": round(current_value, 2),
                    "qty": round(qty, 8 if "/" in symbol else 6),
                    "target_value": round(target_value_by_symbol.get(symbol, 0.0), 2),
                }
            )
        rows.sort(key=lambda row: row["current_value"], reverse=True)
        return {
            "as_of": as_of,
            "strategy": "Khanna daily-bar copy-trade",
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "allocated": round(allocated_total, 2),
            "target_queue": result.get("active_queue") or [],
            "positions": rows,
        }

    def _tif_for(self, symbol: str):
        return TimeInForce.GTC if "/" in symbol else TimeInForce.DAY

    def _qty_precision_for(self, symbol: str) -> int:
        return 8 if "/" in symbol else 6

    def cancel_open_orders(self) -> int:
        open_orders = basket_bot.trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        canceled = 0
        for order in open_orders:
            if getattr(order, "id", None) is None:
                continue
            basket_bot.trading.cancel_order_by_id(order.id)
            canceled += 1
            self.logger.info("Canceled stale open order %s %s", getattr(order, "symbol", "?"), order.id)
        return canceled

    def _log_order_submission(self, *, symbol: str, side: str, notional: float, alpaca_request: dict, order, rationale: str, state: dict):
        submitted_at = basket_bot._format_order_timestamp(getattr(order, "submitted_at", None))
        trade_log.log_order(
            symbol,
            order.id,
            side.upper(),
            notional,
            alpaca_request=json.dumps(alpaca_request, sort_keys=True),
            rationale=rationale,
            submitted_at=submitted_at,
        )
        basket_bot.log_decision(
            "order_submitted",
            symbol=symbol,
            rationale=rationale,
            state=state,
            order={
                "id": str(order.id),
                "status": str(order.status),
                "alpaca_request": alpaca_request,
                "submitted_at": submitted_at,
                "executed_at": None,
            },
        )

    def submit_buy_notional(self, symbol: str, notional: float, rationale: str, state: dict):
        if notional <= 1.0:
            return None
        tif = self._tif_for(symbol)
        alpaca_request = basket_bot._order_request_payload(symbol=symbol, side="buy", notional=round(notional, 2), time_in_force=tif)
        order = basket_bot.trading.submit_order(
            MarketOrderRequest(symbol=symbol, notional=round(notional, 2), side=OrderSide.BUY, time_in_force=tif)
        )
        self.logger.info(f"BUY ${notional:.2f} {symbol} rationale={rationale}")
        self._log_order_submission(symbol=symbol, side="buy", notional=round(notional, 2), alpaca_request=alpaca_request, order=order, rationale=rationale, state=state)
        return order

    def submit_sell_qty(self, symbol: str, qty: float, reference_price: float, rationale: str, state: dict):
        qty = round(qty, self._qty_precision_for(symbol))
        if qty <= 0:
            return None
        tif = self._tif_for(symbol)
        alpaca_request = basket_bot._order_request_payload(symbol=symbol, side="sell", qty=qty, time_in_force=tif)
        order = basket_bot.trading.submit_order(
            MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.SELL, time_in_force=tif)
        )
        self.logger.info(f"SELL {qty} {symbol} rationale={rationale}")
        self._log_order_submission(
            symbol=symbol,
            side="sell",
            notional=round(qty * reference_price, 2),
            alpaca_request=alpaca_request,
            order=order,
            rationale=rationale,
            state=state | {"qty": qty, "reference_price": round(reference_price, 2)},
        )
        return order

    def settle_sell_orders(self):
        for _ in range(10):
            open_orders = basket_bot.trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
            if not any(order.side == OrderSide.SELL for order in open_orders):
                break
            time.sleep(1)

    def rebalance_to_weights(self, target_weights: dict[str, float], result: dict, reason: str):
        equity, target_value_by_symbol = self._target_value_by_symbol(target_weights)
        positions = self.current_positions()

        sells: list[tuple[float, str, object]] = []
        for symbol, position in positions.items():
            current_value = float(position.market_value)
            target_value = target_value_by_symbol.get(symbol, 0.0)
            excess = round(current_value - target_value, 2)
            if excess > 1.0:
                sells.append((excess, symbol, position))

        for excess, symbol, position in sorted(sells, reverse=True):
            current_qty = float(position.qty)
            current_price = float(position.current_price)
            qty = current_qty if target_value_by_symbol.get(symbol, 0.0) <= 0 else min(current_qty, excess / current_price if current_price > 0 else current_qty)
            rationale = basket_bot._versioned_rationale(reason)
            self.submit_sell_qty(
                symbol,
                qty,
                current_price,
                rationale,
                {
                    "trigger_type": "copytrade_rebalance",
                    "target_weight": target_weights.get(symbol, 0.0),
                    "target_value": target_value_by_symbol.get(symbol, 0.0),
                    "current_value": round(current_value, 2),
                    "excess": excess,
                    "active_queue": result.get("active_queue") or [],
                },
            )

        self.settle_sell_orders()

        refreshed_positions = self.current_positions()
        buys: list[tuple[float, str]] = []
        for symbol, target_value in target_value_by_symbol.items():
            current_value = float(getattr(refreshed_positions.get(symbol), "market_value", 0.0) or 0.0)
            deficit = round(target_value - current_value, 2)
            if deficit > 1.0:
                buys.append((deficit, symbol))

        for deficit, symbol in sorted(buys, reverse=True):
            cash = float(basket_bot.trading.get_account().cash)
            spend = min(deficit, cash)
            if spend <= 1.0:
                continue
            rationale = basket_bot._versioned_rationale(reason)
            self.submit_buy_notional(
                symbol,
                spend,
                rationale,
                {
                    "trigger_type": "copytrade_rebalance",
                    "target_weight": target_weights[symbol],
                    "target_value": target_value_by_symbol[symbol],
                    "deficit": deficit,
                    "cash_available": round(cash, 2),
                    "active_queue": result.get("active_queue") or [],
                },
            )

    def _latest_rebalance_rows_by_symbol(self, rationale: str) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        for row in reversed(trade_log.all_rows()):
            symbol = row.get("symbol")
            if not symbol or symbol in latest:
                continue
            if not self._matches_rationale(row.get("rationale"), rationale):
                continue
            latest[symbol] = row
        return latest

    def _base_rationale_reason(self, rationale: str | None) -> str:
        text = str(rationale or "").strip()
        if "->" in text:
            text = text.split("->", 1)[1]
        if " [attempt " in text:
            text = text.split(" [attempt ", 1)[0]
        return text.strip()

    def _matches_rationale(self, candidate: str | None, rationale: str) -> bool:
        return self._base_rationale_reason(candidate) == self._base_rationale_reason(rationale)

    def _attempt_count(self, rationale: str, symbol: str, side: str) -> int:
        normalized_side = side.upper()
        return sum(
            1
            for row in trade_log.all_rows()
            if self._matches_rationale(row.get("rationale"), rationale)
            and row.get("symbol") == symbol
            and str(row.get("side") or "").upper() == normalized_side
        )

    def complete_incomplete_orders(self, target_weights: dict[str, float], result: dict, reason: str) -> int:
        base_rationale = basket_bot._versioned_rationale(reason)
        latest_rows = self._latest_rebalance_rows_by_symbol(base_rationale)
        if not latest_rows:
            return 0

        _, target_value_by_symbol = self._target_value_by_symbol(target_weights)
        positions = self.current_positions()
        submitted = 0

        sell_statuses = {"pending", "canceled", "cancelled", "partial_fill_canceled", "partial_fill_expired", "partial_fill_rejected"}
        buy_statuses = sell_statuses

        sells: list[tuple[str, float, float, dict]] = []
        for symbol, row in latest_rows.items():
            if str(row.get("side") or "").upper() != "SELL":
                continue
            if str(row.get("status") or "").lower() not in sell_statuses:
                continue
            position = positions.get(symbol)
            current_qty = float(getattr(position, "qty", 0.0) or 0.0)
            current_price = float(getattr(position, "current_price", 0.0) or 0.0)
            current_value = float(getattr(position, "market_value", 0.0) or 0.0)
            target_value = target_value_by_symbol.get(symbol, 0.0)
            excess = round(current_value - target_value, 2)
            if current_qty <= 0 or current_price <= 0 or excess <= 1.0:
                continue
            qty = current_qty if target_value <= 0 else min(current_qty, excess / current_price)
            if qty <= 0:
                continue
            sells.append((symbol, qty, current_price, row))

        for symbol, qty, current_price, row in sells:
            attempt_count = self._attempt_count(base_rationale, symbol, "SELL")
            if attempt_count >= MAX_COMPLETION_ATTEMPTS_PER_ASSET:
                self.logger.warning(
                    "Skipping SELL retry for %s; already reached %s attempts for the current Khanna rebalance.",
                    symbol,
                    MAX_COMPLETION_ATTEMPTS_PER_ASSET,
                )
                continue
            rationale = f"{base_rationale} [attempt {attempt_count + 1}/{MAX_COMPLETION_ATTEMPTS_PER_ASSET}]"
            self.logger.info("Completing unfinished SELL %s from the prior Khanna rebalance.", symbol)
            self.submit_sell_qty(
                symbol,
                qty,
                current_price,
                rationale,
                {
                    "trigger_type": "copytrade_completion",
                    "completion_of_order_id": row.get("order_id"),
                    "previous_status": row.get("status"),
                    "target_weight": target_weights.get(symbol, 0.0),
                    "target_value": target_value_by_symbol.get(symbol, 0.0),
                    "active_queue": result.get("active_queue") or [],
                },
            )
            submitted += 1

        if sells:
            self.settle_sell_orders()
            positions = self.current_positions()

        buys: list[tuple[str, float, dict]] = []
        for symbol, row in latest_rows.items():
            if str(row.get("side") or "").upper() != "BUY":
                continue
            if str(row.get("status") or "").lower() not in buy_statuses:
                continue
            current_value = float(getattr(positions.get(symbol), "market_value", 0.0) or 0.0)
            target_value = target_value_by_symbol.get(symbol, 0.0)
            deficit = round(target_value - current_value, 2)
            if deficit <= 1.0:
                continue
            buys.append((symbol, deficit, row))

        for symbol, deficit, row in sorted(buys, key=lambda item: item[1], reverse=True):
            attempt_count = self._attempt_count(base_rationale, symbol, "BUY")
            if attempt_count >= MAX_COMPLETION_ATTEMPTS_PER_ASSET:
                self.logger.warning(
                    "Skipping BUY retry for %s; already reached %s attempts for the current Khanna rebalance.",
                    symbol,
                    MAX_COMPLETION_ATTEMPTS_PER_ASSET,
                )
                continue
            cash = float(basket_bot.trading.get_account().cash)
            spend = min(deficit, cash)
            if spend <= 1.0:
                continue
            rationale = f"{base_rationale} [attempt {attempt_count + 1}/{MAX_COMPLETION_ATTEMPTS_PER_ASSET}]"
            self.logger.info("Completing unfinished BUY %s from the prior Khanna rebalance.", symbol)
            self.submit_buy_notional(
                symbol,
                spend,
                rationale,
                {
                    "trigger_type": "copytrade_completion",
                    "completion_of_order_id": row.get("order_id"),
                    "previous_status": row.get("status"),
                    "target_weight": target_weights.get(symbol, 0.0),
                    "target_value": target_value_by_symbol.get(symbol, 0.0),
                    "deficit": deficit,
                    "cash_available": round(cash, 2),
                    "active_queue": result.get("active_queue") or [],
                },
            )
            submitted += 1
        return submitted

    def evaluate(self, *, force: bool = False, reason: str):
        canceled_orders = self.cancel_open_orders()
        if canceled_orders:
            self.logger.info("Canceled %s stale open order(s) before evaluating the Khanna book.", canceled_orders)

        as_of = self.now_et().date().isoformat()
        result = self.simulate_target_book(as_of)
        target_weights = _weights_from_simulation(result)
        signature = _signature_for(result, target_weights)
        is_same_target = signature == self.last_rebalance_signature
        if not force and is_same_target:
            if not self.market_open():
                return
            completion_orders = self.complete_incomplete_orders(target_weights, result, reason)
            if completion_orders:
                self.order_sync.sync_trade_log_until_settled()
                self.save_state()
            return

        if not self.market_open():
            self.logger.info("Signal state changed but market is closed. Waiting for the next session.")
            return

        self.logger.info(
            "Applying Khanna daily-bar copy-trade book: queue=%s effective_queue=%s symbols=%s trade_window=%s→%s",
            MAX_NAMES,
            result.get("effective_queue_limit"),
            len(target_weights),
            (result.get("trade_window") or {}).get("first_trade_day"),
            (result.get("trade_window") or {}).get("last_trade_day"),
        )
        self.rebalance_to_weights(target_weights, result, reason=reason)
        self.order_sync.sync_trade_log_until_settled()
        self.last_rebalance_signature = signature
        self.save_state()

    def startup_sync(self):
        self.refresh_signals_if_due(force=True)
        self.load_state()
        self.order_sync.sync_trade_log_until_settled()
        self.evaluate(force=False, reason="Khanna copy-trade rebalance")
        self.order_sync.sync_trade_log_until_settled()
        self.save_state()
        self.snapshot_publisher.publish_if_due(force=True)

    def run(self):
        try:
            self.startup_sync()
        except Exception as exc:
            self.logger.error(f"STARTUP ERROR: {exc}")
            time.sleep(30)
        while True:
            try:
                self.refresh_signals_if_due()
                pending_count = self.order_sync.sync_trade_log()
                clock = self.market_clock()
                if not clock.is_open:
                    self.logger.info(f"Market closed. Next open {clock.next_open.strftime('%Y-%m-%d %H:%M %Z')}")
                self.evaluate(reason="Khanna copy-trade rebalance")
                if pending_count > 0:
                    self.order_sync.sync_trade_log_until_settled()
                else:
                    self.order_sync.sync_trade_log()
                self.save_state()
                self.snapshot_publisher.publish_if_due()
                time.sleep(POLL_INTERVAL)
            except Exception as exc:
                self.logger.error(f"LOOP ERROR: {exc}")
                time.sleep(30)


def main():
    CopyTradeLiveManager().run()
