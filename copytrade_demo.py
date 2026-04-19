"""
Capitol Trades copy-trade research runner.

This keeps the simple "follow disclosed buys and sells" idea, but moves the
market-data path onto the same Alpaca-backed local cache used by the main
simulator.

Policy choices:

- signals are keyed off Capitol Trades `published_at` dates, not trade dates
- execution defaults to the next trading day's opening bar
- active copied weights are normalized from tier points to keep capital fully invested
- Capitol size bands map to point tiers: `5M-25M = 20`, `50K+ = 4`, `15K-50K = 2`, `1K-15K = 1`
- Capitol `'< 1K'` disclosures are ignored
- signal points stack on repeated buys and subtract on sells instead of hard-resetting a name
- active point balances can decay by a configurable daily percentage between event days
- active names sit in a capped exit queue: lower bands are closer to the exit
- within the same band, weaker `%` performers move closer to the exit
- large top-tier and band-1 bursts can temporarily expand the queue above the base cap, then the cap steps back down by one on later event days

Usage:
    python3 copytrade_demo.py
    python3 copytrade_demo.py --politician "Markwayne Mullin"
"""

from __future__ import annotations

import argparse
import json
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hourly_strategy import ET, _load_symbol_rows, _utc

HERE = Path(__file__).parent
SIGNALS_PATH = HERE / "copytrade_signals.json"
DEFAULT_MAX_NAMES = 10

BAND_ORDER = ["< 1K", "1K-15K", "15K-50K", "50K-100K", "100K-250K", "250K-500K", "500K-1M", "1M-5M", "5M-25M"]
BAND_RANK = {band: idx for idx, band in enumerate(BAND_ORDER)}
BAND_POINTS = {
    "< 1K": 0,
    "1K-15K": 1,
    "15K-50K": 2,
    "50K-100K": 4,
    "100K-250K": 4,
    "250K-500K": 4,
    "500K-1M": 4,
    "1M-5M": 4,
    "5M-25M": 20,
}


@dataclass(frozen=True)
class DisclosureSignal:
    published_at: str
    traded_at: str
    politician: str
    symbol: str
    side: str
    size_band: str
    source: str


@dataclass(frozen=True)
class DailyQuote:
    day: str
    open: float
    open_ts: str
    close: float
    close_ts: str


@dataclass(frozen=True)
class DailySeries:
    days: list[str]
    quotes: dict[str, DailyQuote]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", default=str(SIGNALS_PATH))
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--min-band", default="50K-100K")
    parser.add_argument("--max-names", type=int, default=DEFAULT_MAX_NAMES)
    parser.add_argument("--politician", default=None)
    parser.add_argument("--entry-lag-trading-days", type=int, default=1)
    parser.add_argument("--daily-decay-pct", type=float, default=0.0)
    parser.add_argument("--end", default=date.today().isoformat())
    return parser.parse_args()


def load_signals(path: Path, politician: str | None = None) -> list[DisclosureSignal]:
    raw = json.loads(path.read_text())
    signals = [DisclosureSignal(**item) for item in raw]
    if politician is None:
        return signals
    return [signal for signal in signals if signal.politician == politician]


def qualifies(signal: DisclosureSignal, min_band: str) -> bool:
    if signal.size_band not in BAND_RANK or min_band not in BAND_RANK:
        return False
    return BAND_RANK[signal.size_band] >= BAND_RANK[min_band]


def target_points(signal: DisclosureSignal) -> int:
    return BAND_POINTS.get(signal.size_band, 0)


def _queue_bucket(points: int) -> int:
    if points <= 1:
        return 0
    if points <= 2:
        return 1
    if points <= 4:
        return 2
    return 3


def _day_from_ts(ts: str) -> str:
    return _utc(ts).astimezone(ET).date().isoformat()


def _build_daily_series(rows: dict[str, tuple[float, float, float, float]]) -> DailySeries:
    quotes: dict[str, DailyQuote] = {}
    for ts in sorted(rows):
        open_, close, _, _ = rows[ts]
        day = _day_from_ts(ts)
        existing = quotes.get(day)
        if existing is None:
            quotes[day] = DailyQuote(day=day, open=open_, open_ts=ts, close=close, close_ts=ts)
            continue
        quotes[day] = DailyQuote(
            day=day,
            open=existing.open,
            open_ts=existing.open_ts,
            close=close,
            close_ts=ts,
        )
    return DailySeries(days=sorted(quotes), quotes=quotes)


def load_market_series(symbols: list[str], start: str, end: str) -> tuple[list[str], dict[str, DailySeries], dict[str, str]]:
    start_dt = datetime.fromisoformat(f"{start}T00:00:00+00:00")
    end_dt = datetime.fromisoformat(f"{end}T00:00:00+00:00") + timedelta(days=1)
    calendar_rows = _load_symbol_rows("SPY", start=start, end=end_dt.date().isoformat(), start_dt=start_dt, end_dt=end_dt)
    calendar_series = _build_daily_series(calendar_rows)

    market: dict[str, DailySeries] = {"SPY": calendar_series}
    skipped: dict[str, str] = {}
    for symbol in sorted(set(symbols)):
        rows = _load_symbol_rows(symbol, start=start, end=end_dt.date().isoformat(), start_dt=start_dt, end_dt=end_dt)
        if not rows:
            skipped[symbol] = "no market data"
            continue
        series = _build_daily_series(rows)
        if not series.days:
            skipped[symbol] = "no daily quotes"
            continue
        market[symbol] = series
    return calendar_series.days, market, skipped


def _quote_on_or_after(series: DailySeries, day: str, field: str) -> tuple[str, float, str] | None:
    idx = bisect_left(series.days, day)
    if idx >= len(series.days):
        return None
    quote = series.quotes[series.days[idx]]
    if field == "open":
        return quote.day, quote.open, quote.open_ts
    return quote.day, quote.close, quote.close_ts


def _quote_on_or_before(series: DailySeries, day: str, field: str) -> tuple[str, float, str] | None:
    idx = bisect_right(series.days, day) - 1
    if idx < 0:
        return None
    quote = series.quotes[series.days[idx]]
    if field == "open":
        return quote.day, quote.open, quote.open_ts
    return quote.day, quote.close, quote.close_ts


def _trade_day_for_signal(published_at: str, trading_days: list[str], lag: int) -> str | None:
    if lag < 0:
        raise ValueError("entry_lag_trading_days must be >= 0")
    idx = bisect_left(trading_days, published_at)
    idx += lag
    if idx >= len(trading_days):
        return None
    return trading_days[idx]


def _desired_weights(raw_points: dict[str, float]) -> dict[str, float]:
    active = {symbol: max(0.0, points) for symbol, points in raw_points.items() if points > 0}
    total = sum(active.values())
    if total <= 0:
        return {}
    return {symbol: points / total for symbol, points in active.items()}


def _portfolio_value_on_day(day: str, cash: float, positions: dict[str, float], market: dict[str, DailySeries]) -> float:
    total = cash
    for symbol, qty in positions.items():
        if qty <= 0:
            continue
        series = market.get(symbol)
        if series is None:
            continue
        exact = series.quotes.get(day)
        if exact is not None:
            total += qty * exact.open
            continue
        quote = _quote_on_or_before(series, day, "close")
        if quote is None:
            continue
        total += qty * quote[1]
    return round(total, 2)


def _signal_point_deltas(signals: list[DisclosureSignal]) -> dict[str, float]:
    deltas: dict[str, float] = defaultdict(float)
    for signal in signals:
        points = float(target_points(signal))
        if points <= 0:
            continue
        if signal.side == "buy":
            deltas[signal.symbol] += points
        elif signal.side == "sell":
            deltas[signal.symbol] -= points
    return dict(deltas)


def _apply_decay(raw_points: dict[str, float], days_elapsed: int, daily_decay_pct: float) -> None:
    if days_elapsed <= 0 or daily_decay_pct <= 0:
        return
    decay_multiplier = max(0.0, 1.0 - daily_decay_pct) ** days_elapsed
    for symbol in list(raw_points):
        raw_points[symbol] *= decay_multiplier
        if raw_points[symbol] <= 1e-9:
            raw_points.pop(symbol, None)


def _queue_insert(queue: list[str], symbol: str, points: float, active_points: dict[str, float]) -> None:
    bucket = _queue_bucket(points)
    insert_at = len(queue)
    for idx, queued_symbol in enumerate(queue):
        if _queue_bucket(active_points[queued_symbol]) > bucket:
            insert_at = idx
            break
    queue.insert(insert_at, symbol)


def _performance_pct(
    symbol: str,
    *,
    positions: dict[str, float],
    cost_basis: dict[str, float],
    market: dict[str, DailySeries],
    day: str,
) -> float:
    qty = positions.get(symbol, 0.0)
    avg_cost = cost_basis.get(symbol, 0.0)
    if qty <= 0 or avg_cost <= 0:
        return 0.0
    series = market.get(symbol)
    if series is None:
        return 0.0
    quote = _quote_on_or_after(series, day, "open")
    if quote is None:
        return 0.0
    price = quote[1]
    return (price / avg_cost) - 1.0


def _resort_queue(
    queue: list[str],
    *,
    active_points: dict[str, float],
    positions: dict[str, float],
    cost_basis: dict[str, float],
    market: dict[str, DailySeries],
    day: str,
    entry_order: dict[str, int],
) -> list[str]:
    return sorted(
        queue,
        key=lambda symbol: (
            _queue_bucket(active_points[symbol]),
            _performance_pct(symbol, positions=positions, cost_basis=cost_basis, market=market, day=day),
            entry_order.get(symbol, 0),
        ),
    )


def _spy_buy_and_hold(capital: float, start_day: str, end_day: str, market: dict[str, DailySeries]) -> dict | None:
    series = market.get("SPY")
    if series is None:
        return None
    entry = _quote_on_or_after(series, start_day, "open")
    exit_ = _quote_on_or_before(series, end_day, "close")
    if entry is None or exit_ is None:
        return None
    qty = capital / entry[1]
    final = round(qty * exit_[1], 2)
    return {
        "entry_day": entry[0],
        "entry_price": round(entry[1], 2),
        "exit_day": exit_[0],
        "exit_price": round(exit_[1], 2),
        "final_equity": final,
        "return_pct": round((final / capital - 1) * 100, 2),
    }


def simulate_with_market(
    signals: list[DisclosureSignal],
    *,
    market: dict[str, DailySeries],
    trading_days: list[str],
    capital: float,
    min_band: str,
    entry_lag_trading_days: int,
    end: str,
    max_names: int = DEFAULT_MAX_NAMES,
    daily_decay_pct: float = 0.0,
    skipped_symbols: dict[str, str] | None = None,
) -> dict:
    eligible = [signal for signal in signals if qualifies(signal, min_band) and target_points(signal) > 0]
    if not eligible:
        return {
            "capital": capital,
            "weight_mode": "normalized",
            "queue_limit": max_names,
            "effective_queue_limit": max_names,
            "active_queue": [],
            "min_band": min_band,
            "signals_used": 0,
            "events": [],
            "final_equity": capital,
            "return_pct": 0.0,
            "cash": capital,
            "positions": {},
            "benchmarks": {},
        }

    by_trade_day: dict[str, list[DisclosureSignal]] = defaultdict(list)
    dropped_signals: list[dict[str, str]] = []
    for signal in sorted(eligible, key=lambda item: (item.published_at, item.symbol, item.side, item.source)):
        trade_day = _trade_day_for_signal(signal.published_at, trading_days, entry_lag_trading_days)
        if trade_day is None or trade_day > end:
            dropped_signals.append(
                {
                    "published_at": signal.published_at,
                    "symbol": signal.symbol,
                    "reason": "signal arrives after available trade window",
                }
            )
            continue
        by_trade_day[trade_day].append(signal)

    if not by_trade_day:
        return {
            "capital": capital,
            "weight_mode": "normalized",
            "queue_limit": max_names,
            "effective_queue_limit": max_names,
            "active_queue": [],
            "min_band": min_band,
            "signals_used": 0,
            "events": dropped_signals,
            "final_equity": capital,
            "return_pct": 0.0,
            "cash": capital,
            "positions": {},
            "benchmarks": {},
        }

    raw_points: dict[str, float] = defaultdict(float)
    active_queue: list[str] = []
    positions: dict[str, float] = defaultdict(float)
    cost_basis: dict[str, float] = {}
    entry_order: dict[str, int] = {}
    next_entry_order = 0
    effective_max_names = max_names
    cash = capital
    events: list[dict] = []
    previous_trade_day: str | None = None

    for trade_day in sorted(by_trade_day):
        if previous_trade_day is not None:
            days_elapsed = (date.fromisoformat(trade_day) - date.fromisoformat(previous_trade_day)).days
            _apply_decay(raw_points, days_elapsed, daily_decay_pct)
            active_queue = [symbol for symbol in active_queue if raw_points.get(symbol, 0.0) > 1e-9]

        updates = _signal_point_deltas(by_trade_day[trade_day])
        for symbol, point_delta in updates.items():
            if symbol not in market:
                events.append(
                    {
                        "trade_day": trade_day,
                        "published_at": min(signal.published_at for signal in by_trade_day[trade_day] if signal.symbol == symbol),
                        "symbol": symbol,
                        "action": "skip",
                        "reason": skipped_symbols.get(symbol, "missing cached market data") if skipped_symbols else "missing market data",
                    }
                )
                continue
            prior_points = raw_points.get(symbol, 0.0)
            updated_points = max(0.0, prior_points + point_delta)
            if updated_points <= 1e-9:
                raw_points.pop(symbol, None)
                if symbol in active_queue:
                    active_queue.remove(symbol)
                entry_order.pop(symbol, None)
                events.append(
                    {
                        "trade_day": trade_day,
                        "symbol": symbol,
                        "action": "point_update",
                        "point_delta": round(point_delta, 4),
                        "points_after": 0.0,
                    }
                )
                continue
            raw_points[symbol] = updated_points
            if symbol not in active_queue:
                entry_order[symbol] = next_entry_order
                next_entry_order += 1
                _queue_insert(active_queue, symbol, updated_points, raw_points)
            events.append(
                {
                    "trade_day": trade_day,
                    "symbol": symbol,
                    "action": "point_update",
                    "point_delta": round(point_delta, 4),
                    "points_after": round(updated_points, 4),
                }
            )

        active_queue = _resort_queue(
            active_queue,
            active_points=raw_points,
            positions=positions,
            cost_basis=cost_basis,
            market=market,
            day=trade_day,
            entry_order=entry_order,
        )

        priority_count = sum(1 for symbol in active_queue if raw_points[symbol] >= 4)
        if priority_count > effective_max_names:
            effective_max_names = priority_count
        elif effective_max_names > max_names:
            effective_max_names -= 1

        while len(active_queue) > effective_max_names:
            evicted = active_queue.pop(0)
            raw_points.pop(evicted, None)
            cost_basis.pop(evicted, None)
            entry_order.pop(evicted, None)
            events.append(
                {
                    "trade_day": trade_day,
                    "symbol": evicted,
                    "action": "queue_evict",
                    "reason": f"queue limit {effective_max_names}",
                }
            )

        previous_trade_day = trade_day

        target_weights = _desired_weights(raw_points)
        total_equity = _portfolio_value_on_day(trade_day, cash, positions, market)

        trade_plan: list[dict] = []
        active_symbols = sorted({symbol for symbol, qty in positions.items() if qty > 0} | set(target_weights))
        for symbol in active_symbols:
            series = market.get(symbol)
            if series is None:
                continue
            fill = _quote_on_or_after(series, trade_day, "open")
            if fill is None or fill[0] > end:
                events.append(
                    {
                        "trade_day": trade_day,
                        "symbol": symbol,
                        "action": "skip",
                        "reason": "no fillable quote within analysis window",
                    }
                )
                continue
            _, price, fill_ts = fill
            current_value = positions[symbol] * price
            desired_value = total_equity * target_weights.get(symbol, 0.0)
            trade_plan.append(
                {
                    "symbol": symbol,
                    "price": price,
                    "fill_ts": fill_ts,
                    "delta": desired_value - current_value,
                    "current_value": current_value,
                    "desired_value": desired_value,
                    "target_weight": round(target_weights.get(symbol, 0.0), 4),
                }
            )

        for item in sorted((plan for plan in trade_plan if plan["delta"] < -1.0), key=lambda plan: plan["delta"]):
            symbol = item["symbol"]
            sell_value = min(item["current_value"], abs(item["delta"]))
            if sell_value < 1.0:
                continue
            qty = sell_value / item["price"]
            positions[symbol] = max(0.0, positions[symbol] - qty)
            if positions[symbol] <= 1e-9:
                positions[symbol] = 0.0
                cost_basis.pop(symbol, None)
            cash = round(cash + sell_value, 2)
            events.append(
                {
                    "trade_day": trade_day,
                    "fill_ts": item["fill_ts"],
                    "symbol": symbol,
                    "action": "sell",
                    "price": round(item["price"], 2),
                    "amount": round(sell_value, 2),
                    "target_weight": item["target_weight"],
                }
            )

        for item in sorted((plan for plan in trade_plan if plan["delta"] > 1.0), key=lambda plan: plan["delta"], reverse=True):
            symbol = item["symbol"]
            spend = min(item["delta"], cash)
            if spend < 1.0:
                continue
            qty = spend / item["price"]
            prior_qty = positions[symbol]
            prior_cost = cost_basis.get(symbol, item["price"])
            positions[symbol] += qty
            total_qty = positions[symbol]
            if total_qty > 0:
                cost_basis[symbol] = ((prior_qty * prior_cost) + spend) / total_qty
            cash = round(cash - spend, 2)
            events.append(
                {
                    "trade_day": trade_day,
                    "fill_ts": item["fill_ts"],
                    "symbol": symbol,
                    "action": "buy",
                    "price": round(item["price"], 2),
                    "amount": round(spend, 2),
                    "target_weight": item["target_weight"],
                }
            )

    last_trade_day = max(day for day in trading_days if day <= end)
    final_equity = _portfolio_value_on_day(last_trade_day, cash, positions, market)
    open_positions = {}
    for symbol, qty in positions.items():
        if qty <= 0:
            continue
        quote = _quote_on_or_before(market[symbol], last_trade_day, "close")
        if quote is None:
            continue
        open_positions[symbol] = {
            "qty": round(qty, 6),
            "price": round(quote[1], 2),
            "value": round(qty * quote[1], 2),
            "weight": round((qty * quote[1]) / final_equity, 4) if final_equity else 0.0,
        }

    first_trade_day = min(by_trade_day)
    benchmarks: dict[str, dict] = {}
    spy_benchmark = _spy_buy_and_hold(capital, first_trade_day, last_trade_day, market)
    if spy_benchmark is not None:
        benchmarks["SPY_buy_and_hold"] = spy_benchmark

    signal_symbols = sorted({signal.symbol for signal in eligible})
    return {
        "capital": capital,
        "weight_mode": "normalized",
        "point_system": {"5M-25M": 20, "50K+": 4, "15K-50K": 2, "1K-15K": 1},
        "daily_decay_pct": daily_decay_pct,
        "queue_limit": max_names,
        "effective_queue_limit": effective_max_names,
        "active_queue": active_queue,
        "min_band": min_band,
        "entry_lag_trading_days": entry_lag_trading_days,
        "politicians": sorted({signal.politician for signal in eligible}),
        "signal_symbols": signal_symbols,
        "signals_used": len(eligible),
        "dropped_signals": dropped_signals,
        "events": events,
        "final_equity": final_equity,
        "return_pct": round((final_equity / capital - 1) * 100, 2),
        "cash": round(cash, 2),
        "positions": open_positions,
        "signal_window": {
            "first_published_at": min(signal.published_at for signal in eligible),
            "last_published_at": max(signal.published_at for signal in eligible),
        },
        "trade_window": {
            "first_trade_day": first_trade_day,
            "last_trade_day": last_trade_day,
        },
        "skipped_symbols": skipped_symbols or {},
        "benchmarks": benchmarks,
    }


def simulate(
    signals: list[DisclosureSignal],
    *,
    capital: float,
    min_band: str,
    max_names: int = DEFAULT_MAX_NAMES,
    entry_lag_trading_days: int = 1,
    daily_decay_pct: float = 0.0,
    end: str | None = None,
) -> dict:
    if end is None:
        end = date.today().isoformat()
    eligible = [signal for signal in signals if qualifies(signal, min_band) and target_points(signal) > 0]
    if not eligible:
        return {
            "capital": capital,
            "weight_mode": "normalized",
            "queue_limit": max_names,
            "effective_queue_limit": max_names,
            "active_queue": [],
            "min_band": min_band,
            "signals_used": 0,
            "events": [],
            "final_equity": capital,
            "return_pct": 0.0,
            "cash": capital,
            "positions": {},
            "benchmarks": {},
        }
    start = min(signal.published_at for signal in eligible)
    symbols = sorted({signal.symbol for signal in eligible})
    trading_days, market, skipped_symbols = load_market_series(symbols, start=start, end=end)
    return simulate_with_market(
        signals,
        market=market,
        trading_days=trading_days,
        capital=capital,
        min_band=min_band,
        max_names=max_names,
        entry_lag_trading_days=entry_lag_trading_days,
        daily_decay_pct=daily_decay_pct,
        end=end,
        skipped_symbols=skipped_symbols,
    )


def main():
    args = parse_args()
    signals = load_signals(Path(args.signals), politician=args.politician)
    result = simulate(
        signals,
        capital=args.capital,
        min_band=args.min_band,
        max_names=args.max_names,
        entry_lag_trading_days=args.entry_lag_trading_days,
        daily_decay_pct=args.daily_decay_pct,
        end=args.end,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
