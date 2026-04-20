from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.enums import Adjustment
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

import copytrade_demo as demo
import hourly_strategy

HERE = Path(__file__).resolve().parent.parent
CACHE_ROOT = HERE / "_cache"
CACHE_DIR = CACHE_ROOT / "daily_bars"
SYMBOL_CACHE_DIR = CACHE_DIR / "symbols"
POLITICIANS_CACHE_DIR = CACHE_ROOT / "politicians"
REJECTED_SYMBOLS_PATH = POLITICIANS_CACHE_DIR / "rejected_symbols.json"


def _daily_cache_path(symbol: str, quarter_start: str) -> Path:
    safe_symbol = hourly_strategy._symbol_cache_stem(symbol)
    return SYMBOL_CACHE_DIR / quarter_start[:4] / hourly_strategy._quarter_label(quarter_start) / f"{safe_symbol}.json"


def _rows_to_jsonable(rows: dict[str, tuple[float, float, float, float]]) -> dict[str, list[float]]:
    return {day: [open_, close, low, high] for day, (open_, close, low, high) in rows.items()}


def _rows_from_jsonable(payload: dict[str, list[float]]) -> dict[str, tuple[float, float, float, float]]:
    return {day: tuple(values) for day, values in payload.items()}


def _read_daily_cache(symbol: str, quarter_start: str) -> dict[str, tuple[float, float, float, float]] | None:
    path = _daily_cache_path(symbol, quarter_start)
    if not path.exists():
        return None
    try:
        return _rows_from_jsonable(json.loads(path.read_text()))
    except Exception:
        return None


def _load_rejected_symbols() -> dict[str, str]:
    if not REJECTED_SYMBOLS_PATH.exists():
        return {}
    try:
        payload = json.loads(REJECTED_SYMBOLS_PATH.read_text())
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(symbol): str(reason) for symbol, reason in payload.items()}


def _remember_rejected_symbol(symbol: str, reason: str) -> None:
    rejected = _load_rejected_symbols()
    if rejected.get(symbol) == reason:
        return
    rejected[symbol] = reason
    REJECTED_SYMBOLS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REJECTED_SYMBOLS_PATH.write_text(json.dumps(rejected, indent=2, sort_keys=True))


def _write_daily_cache(symbol: str, quarter_start: str, rows: dict[str, tuple[float, float, float, float]]) -> None:
    path = _daily_cache_path(symbol, quarter_start)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_rows_to_jsonable(rows), sort_keys=True))


def _daily_rows_from_hourly_rows(rows: dict[str, tuple[float, float, float, float]]) -> dict[str, tuple[float, float, float, float]]:
    by_day: dict[str, tuple[float, float, float, float]] = {}
    for ts in sorted(rows):
        open_, close, low, high = rows[ts]
        day = demo._day_from_ts(ts)
        current = by_day.get(day)
        if current is None:
            by_day[day] = (open_, close, low, high)
            continue
        by_day[day] = (
            current[0],
            close,
            min(current[2], low),
            max(current[3], high),
        )
    return by_day


def _fetch_daily_rows(
    symbol: str,
    *,
    start_dt: datetime,
    end_dt: datetime,
) -> dict[str, tuple[float, float, float, float]]:
    rows: dict[str, tuple[float, float, float, float]] = {}
    effective_end_dt = hourly_strategy._effective_market_data_end(symbol, end_dt)
    if effective_end_dt <= start_dt:
        return rows

    request_symbol = hourly_strategy.market_data_symbol(symbol)
    if hourly_strategy.is_crypto_symbol(symbol):
        for chunk_start, chunk_end in hourly_strategy._chunk_ranges(start_dt, effective_end_dt):
            response = hourly_strategy._crypto_data.get_crypto_bars(
                CryptoBarsRequest(
                    symbol_or_symbols=request_symbol,
                    timeframe=TimeFrame.Day,
                    start=chunk_start,
                    end=chunk_end,
                )
            )
            for bar in response.data.get(request_symbol, []):
                day = bar.timestamp.astimezone(timezone.utc).date().isoformat()
                rows[day] = (
                    float(bar.open),
                    float(bar.close),
                    float(bar.low),
                    float(bar.high),
                )
        return rows

    for chunk_start, chunk_end in hourly_strategy._chunk_ranges(start_dt, effective_end_dt):
        response = hourly_strategy._stock_data.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=request_symbol,
                timeframe=TimeFrame.Day,
                start=chunk_start,
                end=chunk_end,
                adjustment=Adjustment.ALL,
            )
        )
        for bar in response.data.get(request_symbol, []):
            day = bar.timestamp.astimezone(timezone.utc).date().isoformat()
            rows[day] = (
                float(bar.open),
                float(bar.close),
                float(bar.low),
                float(bar.high),
            )
    return rows


def _load_symbol_daily_rows(symbol: str, *, start: str, end: str) -> dict[str, tuple[float, float, float, float]]:
    start_dt = datetime.fromisoformat(f"{start}T00:00:00+00:00")
    end_dt = datetime.fromisoformat(f"{end}T00:00:00+00:00") + timedelta(days=1)
    merged: dict[str, tuple[float, float, float, float]] = {}
    for quarter_start, quarter_end in hourly_strategy._quarter_ranges(start_dt, end_dt):
        quarter_start_s = quarter_start.date().isoformat()
        quarter_end_s = quarter_end.date().isoformat()
        cached = _read_daily_cache(symbol, quarter_start_s)
        if cached is None:
            hourly_rows = hourly_strategy._read_cached_quarter_rows(symbol, quarter_start_s, quarter_end_s)
            if hourly_rows is not None:
                cached = _daily_rows_from_hourly_rows(hourly_rows)
                _write_daily_cache(symbol, quarter_start_s, cached)
            else:
                fetched = _fetch_daily_rows(symbol, start_dt=quarter_start, end_dt=quarter_end)
                cached = {
                    day: row
                    for day, row in fetched.items()
                    if start <= day <= end
                }
                _write_daily_cache(symbol, quarter_start_s, cached)
        merged.update(cached)
    return {day: row for day, row in merged.items() if start <= day <= end}


def load_market_series(
    symbols: list[str],
    *,
    start: str,
    end: str,
    ignored_symbols: set[str] | None = None,
) -> tuple[list[str], dict[str, demo.DailySeries], dict[str, str]]:
    ignored_symbols = ignored_symbols or set()
    rejected_symbols = _load_rejected_symbols()
    calendar_rows = _load_symbol_daily_rows("SPY", start=start, end=end)
    calendar_series = demo._build_daily_series(
        {
            f"{day}T00:00:00Z": row
            for day, row in calendar_rows.items()
        }
    )
    market: dict[str, demo.DailySeries] = {"SPY": calendar_series}
    skipped: dict[str, str] = {}
    for symbol in sorted(set(symbols)):
        if symbol in ignored_symbols:
            skipped[symbol] = "ignored symbol"
            continue
        if symbol in rejected_symbols:
            skipped[symbol] = rejected_symbols[symbol]
            continue
        try:
            daily_rows = _load_symbol_daily_rows(symbol, start=start, end=end)
        except Exception as exc:
            message = str(exc).strip()
            skipped[symbol] = "alpaca rejected symbol" if "invalid symbol" in message.lower() else (message or exc.__class__.__name__)
            if "invalid symbol" in message.lower():
                _remember_rejected_symbol(symbol, skipped[symbol])
            continue
        if not daily_rows:
            skipped[symbol] = "no market data"
            continue
        market[symbol] = demo._build_daily_series(
            {
                f"{day}T00:00:00Z": row
                for day, row in daily_rows.items()
            }
        )
        if not market[symbol].days:
            skipped[symbol] = "no daily quotes"
            market.pop(symbol, None)
    return calendar_series.days, market, skipped
