from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import bot as basket_bot
import copytrade_demo as demo
import trade_log
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)

HERE = Path(__file__).parent
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
IGNORED_SYMBOLS = {"SPX"}
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


def _skip_reason(exc: Exception) -> str:
    message = str(exc).strip()
    if "invalid symbol" in message.lower():
        return "alpaca rejected symbol"
    if message:
        return message
    return exc.__class__.__name__


def _load_market_series_safe(symbols: list[str], start: str, end: str):
    start_dt = datetime.fromisoformat(f"{start}T00:00:00+00:00")
    end_dt = datetime.fromisoformat(f"{end}T00:00:00+00:00") + timedelta(days=1)
    calendar_rows = demo._load_symbol_rows(
        "SPY",
        start=start,
        end=end_dt.date().isoformat(),
        start_dt=start_dt,
        end_dt=end_dt,
    )
    calendar_series = demo._build_daily_series(calendar_rows)

    market: dict[str, object] = {"SPY": calendar_series}
    skipped: dict[str, str] = {}
    for symbol in sorted(set(symbols)):
        if symbol in IGNORED_SYMBOLS:
            skipped[symbol] = "ignored symbol"
            continue
        try:
            rows = demo._load_symbol_rows(
                symbol,
                start=start,
                end=end_dt.date().isoformat(),
                start_dt=start_dt,
                end_dt=end_dt,
            )
        except Exception as exc:
            skipped[symbol] = _skip_reason(exc)
            continue
        if not rows:
            skipped[symbol] = "no market data"
            continue
        series = demo._build_daily_series(rows)
        if not series.days:
            skipped[symbol] = "no daily quotes"
            continue
        market[symbol] = series
    return calendar_series.days, market, skipped


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

    def market_clock(self):
        return basket_bot.trading.get_clock()

    def market_open(self) -> bool:
        return bool(self.market_clock().is_open)

    def now_et(self) -> datetime:
        return self.market_clock().timestamp.astimezone(demo.ET)

    def _signal_mtime(self) -> float:
        return SIGNALS_PATH.stat().st_mtime if SIGNALS_PATH.exists() else 0.0

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
        payload = {"last_rebalance_signature": self.last_rebalance_signature}
        STATE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))

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
                trading_days, market, skipped_symbols = _load_market_series_safe(
                    symbols,
                    start=start,
                    end=as_of,
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
                result = demo.simulate(
                    signals,
                    capital=SIM_CAPITAL,
                    min_band=MIN_BAND,
                    max_names=MAX_NAMES,
                    entry_lag_trading_days=ENTRY_LAG_TRADING_DAYS,
                    daily_decay_pct=DAILY_DECAY_PCT,
                    end=as_of,
                )
        result["point_system"] = dict(LIVE_POINT_SYSTEM)
        self._cached_simulation_key = cache_key
        self._cached_simulation_result = result
        return result

    def current_positions(self) -> dict[str, object]:
        return {_normalize_live_symbol(position.symbol): position for position in basket_bot.trading.get_all_positions()}

    def _tif_for(self, symbol: str):
        return TimeInForce.GTC if "/" in symbol else TimeInForce.DAY

    def _qty_precision_for(self, symbol: str) -> int:
        return 8 if "/" in symbol else 6

    def cancel_open_orders(self) -> int:
        open_orders = basket_bot.trading.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )
        canceled = 0
        for order in open_orders:
            order_id = getattr(order, "id", None)
            symbol = getattr(order, "symbol", "?")
            if order_id is None:
                continue
            basket_bot.trading.cancel_order_by_id(order_id)
            canceled += 1
            self.logger.info("Canceled stale open order %s %s", symbol, order_id)
        return canceled

    def _log_order_submission(
        self,
        *,
        symbol: str,
        side: str,
        notional: float,
        alpaca_request: dict,
        order,
        rationale: str,
        state: dict,
    ):
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
        alpaca_request = basket_bot._order_request_payload(
            symbol=symbol,
            side="buy",
            notional=round(notional, 2),
            time_in_force=tif,
        )
        order = basket_bot.trading.submit_order(
            MarketOrderRequest(
                symbol=symbol,
                notional=round(notional, 2),
                side=OrderSide.BUY,
                time_in_force=tif,
            )
        )
        self.logger.info(f"BUY ${notional:.2f} {symbol} rationale={rationale}")
        self._log_order_submission(
            symbol=symbol,
            side="buy",
            notional=round(notional, 2),
            alpaca_request=alpaca_request,
            order=order,
            rationale=rationale,
            state=state,
        )
        return order

    def submit_sell_qty(
        self,
        symbol: str,
        qty: float,
        reference_price: float,
        rationale: str,
        state: dict,
    ):
        qty = round(qty, self._qty_precision_for(symbol))
        if qty <= 0:
            return None
        tif = self._tif_for(symbol)
        alpaca_request = basket_bot._order_request_payload(
            symbol=symbol,
            side="sell",
            qty=qty,
            time_in_force=tif,
        )
        order = basket_bot.trading.submit_order(
            MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=tif,
            )
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
            open_orders = basket_bot.trading.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
            )
            if not any(order.side == OrderSide.SELL for order in open_orders):
                break
            time.sleep(1)

    def rebalance_to_weights(self, target_weights: dict[str, float], result: dict, reason: str):
        equity = float(basket_bot.trading.get_account().equity)
        positions = self.current_positions()
        target_value_by_symbol = {
            symbol: round(equity * weight, 2) for symbol, weight in target_weights.items()
        }

        sells: list[tuple[float, str, object]] = []
        for symbol, position in positions.items():
            current_value = float(position.market_value)
            target_value = target_value_by_symbol.get(symbol, 0.0)
            excess = round(current_value - target_value, 2)
            if excess <= 1.0:
                continue
            sells.append((excess, symbol, position))

        for excess, symbol, position in sorted(sells, reverse=True):
            current_qty = float(position.qty)
            current_price = float(position.current_price)
            qty = current_qty if target_value_by_symbol.get(symbol, 0.0) <= 0 else min(
                current_qty,
                excess / current_price if current_price > 0 else current_qty,
            )
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

    def evaluate(self, *, force: bool = False, reason: str):
        canceled_orders = self.cancel_open_orders()
        if canceled_orders:
            self.logger.info(
                "Canceled %s stale open order(s) before evaluating the Khanna book.",
                canceled_orders,
            )

        as_of = self.now_et().date().isoformat()
        result = self.simulate_target_book(as_of)
        target_weights = _weights_from_simulation(result)
        signature = _signature_for(result, target_weights)
        if not force and signature == self.last_rebalance_signature:
            return

        if not self.market_open():
            self.logger.info(
                "Signal state changed but market is closed. Waiting for the next session."
            )
            return

        self.logger.info(
            "Applying Khanna copy-trade book: queue=%s effective_queue=%s symbols=%s trade_window=%s→%s",
            MAX_NAMES,
            result.get("effective_queue_limit"),
            len(target_weights),
            (result.get("trade_window") or {}).get("first_trade_day"),
            (result.get("trade_window") or {}).get("last_trade_day"),
        )
        self.rebalance_to_weights(
            target_weights,
            result,
            reason=reason,
        )
        self.order_sync.sync_trade_log()
        self.last_rebalance_signature = signature
        self.save_state()

    def startup_sync(self):
        self.load_state()
        self.order_sync.sync_trade_log()
        self.evaluate(force=False, reason="Khanna copy-trade rebalance")
        self.order_sync.sync_trade_log()
        self.save_state()

    def run(self):
        try:
            self.startup_sync()
        except Exception as exc:
            self.logger.error(f"STARTUP ERROR: {exc}")
            time.sleep(30)
        while True:
            try:
                self.order_sync.sync_trade_log()
                clock = self.market_clock()
                if not clock.is_open:
                    self.logger.info(
                        f"Market closed. Next open {clock.next_open.strftime('%Y-%m-%d %H:%M %Z')}"
                    )
                self.evaluate(reason="Khanna copy-trade rebalance")
                self.order_sync.sync_trade_log()
                self.save_state()
                time.sleep(POLL_INTERVAL)
            except Exception as exc:
                self.logger.error(f"LOOP ERROR: {exc}")
                time.sleep(30)


def main():
    CopyTradeLiveManager().run()


if __name__ == "__main__":
    main()
