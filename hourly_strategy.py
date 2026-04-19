"""
Shared hourly simulator for two related strategies:

- Pure hourly rebalance
- Hourly stop/trigger + rebalance

Both use the same engine and the same stock-session hourly timestamps from
Alpaca historical bars so the comparison stays clean.
"""

from __future__ import annotations

import json
import math
import os
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.enums import Adjustment
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

ET = ZoneInfo("America/New_York")
HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
CACHE_DIR = HERE / ".cache" / "hourly_data"
CACHE_VERSION = 4
SYMBOL_CACHE_DIR = CACHE_DIR / "symbols"

DEFAULT_SYMBOLS = ["TSLA", "TSM", "NVDA", "PLTR", "BTC/USD"]
DEFAULT_TARGET_WEIGHTS = {
    "TSLA": 0.50,
    "TSM": 0.125,
    "NVDA": 0.125,
    "PLTR": 0.125,
    "BTC/USD": 0.125,
}
ABSORBER_SYMBOL = "BTC/USD"
FRACTIONAL_SYMBOLS = {ABSORBER_SYMBOL}
DISPLAY_LABELS = {"TSM": "TSMC", "BTC/USD": "BTC"}
REGULAR_HOURLY_STARTS_ET = {10, 11, 12, 13, 14, 15}
BETA_WINDOW = 60

_api_key = os.getenv("ALPACA_API_KEY")
_secret_key = os.getenv("ALPACA_SECRET_KEY")
_stock_data = StockHistoricalDataClient(api_key=_api_key, secret_key=_secret_key)
_crypto_data = CryptoHistoricalDataClient(api_key=_api_key, secret_key=_secret_key)
_cache: dict[tuple[tuple[str, ...], str, str], dict] = {}
_raw_bar_cache: dict[tuple[str, str, str], dict[str, tuple[float, float, float, float]]] = {}
_cache_lock = Lock()


def display(sym: str) -> str:
    return DISPLAY_LABELS.get(sym, sym)


def is_fractional(sym: str) -> bool:
    return is_crypto_symbol(sym) or sym in FRACTIONAL_SYMBOLS


def is_crypto_symbol(sym: str) -> bool:
    return "/" in sym


def market_data_symbol(sym: str) -> str:
    if is_crypto_symbol(sym):
        return sym
    return sym.replace("-", ".")


def is_absorber(sym: str) -> bool:
    return sym == ABSORBER_SYMBOL


def trades_24x7(sym: str) -> bool:
    return is_crypto_symbol(sym)


def _utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _ts_key(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stock_session_bar(dt: datetime) -> bool:
    et = dt.astimezone(ET)
    return et.weekday() < 5 and et.hour in REGULAR_HOURLY_STARTS_ET


def _quarter_start(dt: datetime) -> datetime:
    month = ((dt.month - 1) // 3) * 3 + 1
    return datetime(dt.year, month, 1, tzinfo=timezone.utc)


def _next_quarter_start(dt: datetime) -> datetime:
    start = _quarter_start(dt)
    year = start.year + (1 if start.month == 10 else 0)
    month = 1 if start.month == 10 else start.month + 3
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _quarter_ranges(start_dt: datetime, end_dt: datetime) -> list[tuple[datetime, datetime]]:
    if start_dt >= end_dt:
        return []
    ranges: list[tuple[datetime, datetime]] = []
    cursor = _quarter_start(start_dt)
    while cursor < end_dt:
        nxt = _next_quarter_start(cursor)
        ranges.append((cursor, nxt))
        cursor = nxt
    return ranges


def _quarter_label(start: str) -> str:
    month = int(start[5:7])
    quarter = ((month - 1) // 3) + 1
    return f"Q{quarter}"


def _symbol_cache_stem(symbol: str) -> str:
    return symbol.replace("/", "__")


def _symbol_disk_cache_path(symbol: str, start: str, end: str) -> Path:
    del end
    safe_symbol = _symbol_cache_stem(symbol)
    return SYMBOL_CACHE_DIR / start[:4] / _quarter_label(start) / f"{safe_symbol}.json"


def _legacy_symbol_disk_cache_path(symbol: str, start: str, end: str) -> Path:
    import hashlib

    cache_key = "|".join([str(CACHE_VERSION), symbol, start, end])
    digest = hashlib.sha256(cache_key.encode()).hexdigest()[:16]
    safe_symbol = _symbol_cache_stem(symbol)
    return SYMBOL_CACHE_DIR / f"{safe_symbol}_{start}_{end}_{digest}.json"


def _chunk_ranges(start_dt: datetime, end_dt: datetime, days: int = 120) -> list[tuple[datetime, datetime]]:
    ranges: list[tuple[datetime, datetime]] = []
    cursor = start_dt
    while cursor < end_dt:
        nxt = min(end_dt, cursor + timedelta(days=days))
        ranges.append((cursor, nxt))
        cursor = nxt
    return ranges


def _effective_market_data_end(symbol: str, requested_end_dt: datetime, *, now_dt: datetime | None = None) -> datetime:
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    latest_allowed = now_dt if is_crypto_symbol(symbol) else now_dt - timedelta(minutes=20)
    return min(requested_end_dt, latest_allowed)


def _compute_rolling_betas(assets: dict, symbols: list[str], n: int) -> dict[str, list[float]]:
    spy_c = assets["SPY"]["closes"]
    spy_r = [spy_c[i] / spy_c[i - 1] - 1 for i in range(1, n)]

    result: dict[str, list[float]] = {}
    for sym in symbols:
        c = assets[sym]["closes"]
        ar = [c[i] / c[i - 1] - 1 for i in range(1, n)]
        series: list[float | None] = [None]
        for i in range(1, n):
            w0 = max(0, i - BETA_WINDOW)
            a_w = ar[w0:i]
            s_w = spy_r[w0:i]
            if len(a_w) < 5:
                series.append(None)
                continue
            k = len(a_w)
            am = sum(a_w) / k
            sm = sum(s_w) / k
            cov = sum((a - am) * (s - sm) for a, s in zip(a_w, s_w)) / k
            var = sum((s - sm) ** 2 for s in s_w) / k
            raw = cov / var if var > 0 else 1.0
            series.append(max(0.3, min(4.0, round(raw, 3))))
        first = next((b for b in series if b is not None), 1.0)
        result[sym] = [b if b is not None else first for b in series]
    return result


def _fill_to_union(
    union: list[str],
    source_rows: dict[str, tuple[float, float, float, float]],
) -> dict[str, list[float]]:
    opens: list[float] = []
    closes: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    first_row = source_rows[min(source_rows)] if source_rows else None
    if first_row is None:
        raise ValueError("Cannot fill empty source rows")
    last_open = None
    last_close = None
    for ts in union:
        row = source_rows.get(ts)
        if row is not None:
            open_, close, low, high = row
            last_open = open_
            last_close = close
        elif last_close is not None:
            open_ = close = low = high = last_close
        else:
            open_ = last_open = first_row[0]
            close = low = high = first_row[1]
        opens.append(round(open_, 4))
        closes.append(round(close, 4))
        lows.append(round(low, 4))
        highs.append(round(high, 4))
    return {"opens": opens, "closes": closes, "lows": lows, "highs": highs}


def _rows_to_jsonable(rows: dict[str, tuple[float, float, float, float]]) -> dict[str, list[float]]:
    return {ts: [open_, close, low, high] for ts, (open_, close, low, high) in rows.items()}


def _rows_from_jsonable(payload: dict[str, list[float]]) -> dict[str, tuple[float, float, float, float]]:
    return {ts: tuple(values) for ts, values in payload.items()}


def _read_cached_quarter_rows(symbol: str, quarter_start: str, quarter_end: str) -> dict[str, tuple[float, float, float, float]] | None:
    cache_key = (symbol, quarter_start, quarter_end)
    with _cache_lock:
        cached = _raw_bar_cache.get(cache_key)
    if cached is not None:
        return cached

    disk_path = _symbol_disk_cache_path(symbol, quarter_start, quarter_end)
    if not disk_path.exists():
        legacy_path = _legacy_symbol_disk_cache_path(symbol, quarter_start, quarter_end)
        if not legacy_path.exists():
            return None
        disk_path = legacy_path
    try:
        cached = _rows_from_jsonable(json.loads(disk_path.read_text()))
    except Exception:
        return None
    canonical_path = _symbol_disk_cache_path(symbol, quarter_start, quarter_end)
    if disk_path != canonical_path:
        _write_cached_quarter_rows(symbol, quarter_start, quarter_end, cached)
    with _cache_lock:
        _raw_bar_cache[cache_key] = cached
    return cached


def _write_cached_quarter_rows(
    symbol: str,
    quarter_start: str,
    quarter_end: str,
    rows: dict[str, tuple[float, float, float, float]],
):
    cache_key = (symbol, quarter_start, quarter_end)
    disk_path = _symbol_disk_cache_path(symbol, quarter_start, quarter_end)
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_text(json.dumps(_rows_to_jsonable(rows)))
    with _cache_lock:
        _raw_bar_cache[cache_key] = rows


def _fetch_symbol_rows(
    symbol: str,
    *,
    start_dt: datetime,
    end_dt: datetime,
) -> dict[str, tuple[float, float, float, float]]:
    rows: dict[str, tuple[float, float, float, float]] = {}
    effective_end_dt = _effective_market_data_end(symbol, end_dt)
    if effective_end_dt <= start_dt:
        return rows
    if is_crypto_symbol(symbol):
        request_symbol = market_data_symbol(symbol)
        for chunk_start, chunk_end in _chunk_ranges(start_dt, effective_end_dt):
            crypto_res = _crypto_data.get_crypto_bars(
                CryptoBarsRequest(
                    symbol_or_symbols=request_symbol,
                    timeframe=TimeFrame.Hour,
                    start=chunk_start,
                    end=chunk_end,
                )
            )
            for bar in crypto_res.data.get(request_symbol, []):
                rows[_ts_key(bar.timestamp)] = (
                    float(bar.open),
                    float(bar.close),
                    float(bar.low),
                    float(bar.high),
                )
        return rows

    request_symbol = market_data_symbol(symbol)
    for chunk_start, chunk_end in _chunk_ranges(start_dt, effective_end_dt):
        stock_res = _stock_data.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=request_symbol,
                timeframe=TimeFrame.Hour,
                start=chunk_start,
                end=chunk_end,
                adjustment=Adjustment.ALL,
            )
        )
        for bar in stock_res.data.get(request_symbol, []):
            if not _stock_session_bar(bar.timestamp):
                continue
            rows[_ts_key(bar.timestamp)] = (
                float(bar.open),
                float(bar.close),
                float(bar.low),
                float(bar.high),
            )
    return rows


def _fetch_stock_rows_batch(
    symbols: list[str],
    *,
    start_dt: datetime,
    end_dt: datetime,
) -> dict[str, dict[str, tuple[float, float, float, float]]]:
    rows_by_symbol = {symbol: {} for symbol in symbols}
    if not symbols:
        return rows_by_symbol
    effective_end_dt = _effective_market_data_end(symbols[0], end_dt)
    if effective_end_dt <= start_dt:
        return rows_by_symbol
    request_symbols = [market_data_symbol(symbol) for symbol in symbols]
    request_to_original = {market_data_symbol(symbol): symbol for symbol in symbols}

    for chunk_start, chunk_end in _chunk_ranges(start_dt, effective_end_dt):
        stock_res = _stock_data.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=request_symbols,
                timeframe=TimeFrame.Hour,
                start=chunk_start,
                end=chunk_end,
                adjustment=Adjustment.ALL,
            )
        )
        for request_symbol, original_symbol in request_to_original.items():
            for bar in stock_res.data.get(request_symbol, []):
                if not _stock_session_bar(bar.timestamp):
                    continue
                rows_by_symbol[original_symbol][_ts_key(bar.timestamp)] = (
                    float(bar.open),
                    float(bar.close),
                    float(bar.low),
                    float(bar.high),
                )
    return rows_by_symbol


def _batched(items: list[str], batch_size: int) -> list[list[str]]:
    return [items[idx : idx + batch_size] for idx in range(0, len(items), batch_size)]


def warm_symbol_cache(
    symbols: list[str],
    *,
    start: str,
    end: str,
    batch_size: int = 20,
) -> dict[str, int]:
    start_dt = datetime.fromisoformat(f"{start}T00:00:00+00:00")
    end_dt = datetime.fromisoformat(f"{end}T00:00:00+00:00")
    stock_symbols = sorted({symbol for symbol in symbols if not is_crypto_symbol(symbol)})
    crypto_symbols = sorted({symbol for symbol in symbols if is_crypto_symbol(symbol)})
    summary = {
        "symbols": len(set(symbols)),
        "quarters": len(_quarter_ranges(start_dt, end_dt)),
        "written": 0,
        "reused": 0,
        "stock_batches": 0,
        "batch_fallbacks": 0,
        "crypto_fetches": 0,
    }

    for quarter_start, quarter_end in _quarter_ranges(start_dt, end_dt):
        quarter_start_s = quarter_start.date().isoformat()
        quarter_end_s = quarter_end.date().isoformat()

        cold_stock_symbols: list[str] = []
        for symbol in stock_symbols:
            cached = _read_cached_quarter_rows(symbol, quarter_start_s, quarter_end_s)
            if cached is None:
                cold_stock_symbols.append(symbol)
            else:
                summary["reused"] += 1

        for batch in _batched(cold_stock_symbols, max(1, batch_size)):
            summary["stock_batches"] += 1
            try:
                fetched_by_symbol = _fetch_stock_rows_batch(batch, start_dt=quarter_start, end_dt=quarter_end)
            except Exception:
                summary["batch_fallbacks"] += 1
                fetched_by_symbol = {
                    symbol: _fetch_symbol_rows(symbol, start_dt=quarter_start, end_dt=quarter_end)
                    for symbol in batch
                }
            for symbol in batch:
                _write_cached_quarter_rows(symbol, quarter_start_s, quarter_end_s, fetched_by_symbol.get(symbol, {}))
                summary["written"] += 1

        for symbol in crypto_symbols:
            cached = _read_cached_quarter_rows(symbol, quarter_start_s, quarter_end_s)
            if cached is not None:
                summary["reused"] += 1
                continue
            rows = _fetch_symbol_rows(symbol, start_dt=quarter_start, end_dt=quarter_end)
            _write_cached_quarter_rows(symbol, quarter_start_s, quarter_end_s, rows)
            summary["written"] += 1
            summary["crypto_fetches"] += 1
    return summary


def _load_symbol_rows(
    symbol: str,
    *,
    start: str,
    end: str,
    start_dt: datetime,
    end_dt: datetime,
) -> dict[str, tuple[float, float, float, float]]:
    merged: dict[str, tuple[float, float, float, float]] = {}
    for quarter_start, quarter_end in _quarter_ranges(start_dt, end_dt):
        quarter_start_s = quarter_start.date().isoformat()
        quarter_end_s = quarter_end.date().isoformat()
        cached = _read_cached_quarter_rows(symbol, quarter_start_s, quarter_end_s)
        if cached is None:
            cached = _fetch_symbol_rows(symbol, start_dt=quarter_start, end_dt=quarter_end)
            _write_cached_quarter_rows(symbol, quarter_start_s, quarter_end_s, cached)
        merged.update(cached)

    start_bound = start_dt
    end_bound = end_dt
    return {
        ts: row
        for ts, row in merged.items()
        if start_bound <= _utc(ts) < end_bound
    }


def load_hourly_data(
    *,
    start: str,
    end: str,
    chosen_symbols: list[str] | None = None,
) -> dict:
    symbols = chosen_symbols[:] if chosen_symbols else DEFAULT_SYMBOLS[:]
    cache_key = (tuple(symbols), start, end)
    with _cache_lock:
        if cache_key in _cache:
            return _cache[cache_key]

    stock_symbols = [sym for sym in symbols if not is_crypto_symbol(sym)]
    crypto_symbols = [sym for sym in symbols if is_crypto_symbol(sym)]
    stock_fetch = stock_symbols + ["SPY"]
    start_dt = datetime.fromisoformat(f"{start}T00:00:00+00:00")
    end_dt = datetime.fromisoformat(f"{end}T00:00:00+00:00")

    raw: dict[str, dict[str, tuple[float, float, float, float]]] = {}
    stock_union: set[str] = set()

    for sym in stock_fetch:
        rows = _load_symbol_rows(sym, start=start, end=end, start_dt=start_dt, end_dt=end_dt)
        raw[sym] = rows
        stock_union.update(rows)

    crypto_union: set[str] = set()
    for sym in crypto_symbols:
        rows = _load_symbol_rows(sym, start=start, end=end, start_dt=start_dt, end_dt=end_dt)
        raw[sym] = rows
        crypto_union.update(rows)

    union = sorted(stock_union | crypto_union)
    stock_timestamps = sorted(stock_union)

    assets: dict[str, dict] = {}
    for sym in stock_fetch + crypto_symbols:
        assets[sym] = _fill_to_union(union, raw[sym])

    beta_assets: dict[str, dict] = {sym: {"closes": []} for sym in symbols + ["SPY"]}
    stock_positions = {ts: idx for idx, ts in enumerate(union)}
    for ts in stock_timestamps:
        idx = stock_positions[ts]
        for sym in symbols + ["SPY"]:
            beta_assets[sym]["closes"].append(assets[sym]["closes"][idx])

    betas_on_stock_grid = _compute_rolling_betas(beta_assets, symbols, len(stock_timestamps))
    betas: dict[str, list[float]] = {}
    stock_beta_idx = {ts: i for i, ts in enumerate(stock_timestamps)}
    for sym in symbols:
        series: list[float] = []
        last_beta = betas_on_stock_grid[sym][0]
        for ts in union:
            idx = stock_beta_idx.get(ts)
            if idx is not None:
                last_beta = betas_on_stock_grid[sym][idx]
            series.append(last_beta)
        betas[sym] = series
    distinct_days: list[str] = []
    for ts in stock_timestamps:
        d = _utc(ts).astimezone(ET).date().isoformat()
        if not distinct_days or distinct_days[-1] != d:
            distinct_days.append(d)

    rebalance_timestamps: set[str] = set()
    current_day = None
    day_bars: list[str] = []
    for ts in stock_timestamps:
        d = _utc(ts).astimezone(ET).date().isoformat()
        if current_day is None:
            current_day = d
        if d != current_day:
            rebalance_timestamps.add(day_bars[-1])
            current_day = d
            day_bars = []
        day_bars.append(ts)
    if day_bars:
        rebalance_timestamps.add(day_bars[-1])

    payload = {
        "timestamps": union,
        "dates": union,
        "trading_days": distinct_days,
        "stock_timestamps": stock_timestamps,
        "rebalance_timestamps": sorted(rebalance_timestamps),
        "assets": assets,
        "betas": betas,
        "avg_betas": {
            display(sym): round(sum(betas_on_stock_grid[sym]) / len(stock_timestamps), 2)
            for sym in symbols
        },
    }
    with _cache_lock:
        _cache[cache_key] = payload
    return payload


@dataclass
class HourlyConfig:
    initial: float = 10_000.0
    target_weights: dict[str, float] | None = None
    fractional_stocks: bool = True
    min_rebalance_notional: float = 25.0
    min_order_notional: float = 25.0
    stock_settlement_days: int = 1
    base_tol: float = 0.02
    trail_step: float = 1.02
    trail_stop: float = 0.99
    stop_sell_pct: float = 0.50
    stop_cooldown_days: int = 1
    rebalance_every_bars: int = 1
    enable_risk_controls: bool = True
    stock_slippage_bps: float = 0.0
    crypto_slippage_bps: float = 0.0
    crypto_taker_fee_bps: float = 0.0
    equity_sec_sell_fee_rate: float = 0.0
    equity_taf_per_share: float = 0.0
    equity_taf_max_per_trade: float = 0.0
    equity_cat_per_share: float = 0.0


def simulate_hourly(
    cfg: HourlyConfig,
    data: dict,
    chosen_symbols: list[str] | None = None,
    *,
    record_events: bool = True,
) -> dict:
    symbols = chosen_symbols[:] if chosen_symbols else DEFAULT_SYMBOLS[:]
    timestamps = data["timestamps"]
    stock_timestamps = set(data["stock_timestamps"])
    rebalance_timestamps = set(data["rebalance_timestamps"])
    assets = data["assets"]
    betas = data["betas"]
    def normalized_target_weights() -> dict[str, float]:
        if cfg.target_weights is not None:
            raw = {sym: float(cfg.target_weights.get(sym, 0.0)) for sym in symbols}
        elif set(symbols).issubset(DEFAULT_TARGET_WEIGHTS):
            raw = {sym: DEFAULT_TARGET_WEIGHTS[sym] for sym in symbols}
        else:
            raw = {sym: 1.0 for sym in symbols}
        total = sum(max(0.0, weight) for weight in raw.values())
        if total <= 0:
            return {sym: 1 / len(symbols) for sym in symbols}
        return {sym: max(0.0, raw[sym]) / total for sym in symbols}

    target_weights = normalized_target_weights()
    trading_days = data["trading_days"]
    day_index = {day: i for i, day in enumerate(trading_days)}
    bar_day = [_utc(ts).astimezone(ET).date().isoformat() for ts in timestamps]

    def next_trading_day(day: str, advance: int) -> str:
        idx = day_index.get(day)
        if idx is None:
            idx = bisect_left(trading_days, day)
        return trading_days[min(len(trading_days) - 1, idx + advance)]

    def floor_pct(sym: str, i: int) -> float:
        return max(0.005, cfg.base_tol * betas[sym][i])

    def trigger_pct(sym: str, i: int) -> float:
        return max(0.005, cfg.base_tol * betas[sym][i])

    def tradable_on_bar(sym: str, i: int) -> bool:
        return trades_24x7(sym) or timestamps[i] in stock_timestamps

    def asset_open(sym: str, i: int) -> float:
        return assets[sym].get("opens", assets[sym]["closes"])[i]

    def uses_fractional_shares(sym: str) -> bool:
        return is_fractional(sym) or (cfg.fractional_stocks and not is_crypto_symbol(sym))

    def next_settlement_day(day: str, sym: str) -> str:
        if is_crypto_symbol(sym):
            return day
        return next_trading_day(day, max(1, cfg.stock_settlement_days))

    def slippage_bps(sym: str) -> float:
        return cfg.crypto_slippage_bps if is_crypto_symbol(sym) else cfg.stock_slippage_bps

    def exec_price(sym: str, price: float, side: str) -> float:
        slip = slippage_bps(sym) / 10_000
        if side == "buy":
            return price * (1 + slip)
        return price * (1 - slip)

    def equity_buy_fee(qty: float) -> float:
        return qty * cfg.equity_cat_per_share

    def equity_sell_fee(qty: float, gross_proceeds: float) -> float:
        sec_fee = gross_proceeds * cfg.equity_sec_sell_fee_rate
        taf_fee = min(qty * cfg.equity_taf_per_share, cfg.equity_taf_max_per_trade)
        cat_fee = qty * cfg.equity_cat_per_share
        return sec_fee + taf_fee + cat_fee

    def buy_qty(sym: str, dollars: float, price: float) -> tuple[float, float]:
        if dollars <= 0:
            return 0.0, 0.0
        fill_price = exec_price(sym, price, "buy")
        if uses_fractional_shares(sym):
            if not is_crypto_symbol(sym):
                per_share = fill_price + cfg.equity_cat_per_share
                qty = dollars / per_share if per_share > 0 else 0.0
                spent = qty * fill_price + equity_buy_fee(qty)
                return qty, round(dollars - spent, 2)
            qty = dollars / fill_price
            qty *= 1 - (cfg.crypto_taker_fee_bps / 10_000) if is_crypto_symbol(sym) else 1.0
            return qty, 0.0
        per_share = fill_price + cfg.equity_cat_per_share
        qty = math.floor(dollars / per_share)
        spent = qty * fill_price + equity_buy_fee(qty)
        return qty, round(dollars - spent, 2)

    def sell_proceeds(sym: str, qty: float, price: float) -> tuple[float, float]:
        fill_price = exec_price(sym, price, "sell")
        gross = qty * fill_price
        if is_crypto_symbol(sym):
            fee = gross * (cfg.crypto_taker_fee_bps / 10_000)
            return round(gross - fee, 2), fill_price
        fee = equity_sell_fee(qty, gross)
        return round(gross - fee, 2), fill_price

    st: dict[str, dict[str, float | str]] = {}
    settled_cash = 0.0
    unsettled_cash: dict[str, float] = {}
    for sym in symbols:
        entry = assets[sym]["closes"][0]
        fp = floor_pct(sym, 0)
        tp = trigger_pct(sym, 0)
        qty, leftover = buy_qty(sym, cfg.initial * target_weights[sym], entry)
        st[sym] = {
            "qty": qty,
            "floor": round(entry * (1 - fp), 2),
            "t_next": round(entry * (1 + tp), 2),
            "stop_ready_day": trading_days[0],
        }
        settled_cash = round(settled_cash + leftover, 2)

    events: list[dict] = []
    history: list[dict] = []
    totals_over_time: list[float] = []
    turnover = 0.0
    rebalance_count = 0
    stop_count = 0
    trail_count = 0

    def evt(i: int, sym: str, action: str, price: float | None, amount: float | None, reason: str):
        if not record_events:
            return
        events.append(
            {
                "date": timestamps[i],
                "symbol": display(sym),
                "action": action,
                "price": round(price, 2) if price is not None else None,
                "amount": round(amount, 2) if amount is not None else None,
                "reason": reason,
            }
        )

    def unsettled_total() -> float:
        return round(sum(unsettled_cash.values()), 2)

    def total_value(i: int) -> float:
        return round(
            settled_cash + unsettled_total() + sum(st[sym]["qty"] * assets[sym]["closes"][i] for sym in symbols),
            2,
        )

    traded_this_bar: set[str] = set()
    next_trade_after: dict[str, list[int | None]] = {}
    for sym in symbols:
        nxt: int | None = None
        series: list[int | None] = [None] * len(timestamps)
        for i in range(len(timestamps) - 1, -1, -1):
            series[i] = nxt
            if tradable_on_bar(sym, i):
                nxt = i
        next_trade_after[sym] = series
    pending_stops: dict[str, dict[str, float | int | str | bool]] = {}

    def can_trade(sym: str) -> bool:
        return sym not in traded_this_bar and sym not in pending_stops

    def mark_traded(sym: str):
        traded_this_bar.add(sym)

    def settle_cash_if_ready(i: int):
        nonlocal settled_cash
        trade_day = bar_day[i]
        released_days = [day for day in unsettled_cash if day <= trade_day]
        if not released_days:
            return
        released = round(sum(unsettled_cash.pop(day, 0.0) for day in released_days), 2)
        if released <= 0:
            return
        settled_cash = round(settled_cash + released, 2)
        evt(i, "CASH", "SETTLEMENT — released", None, released, f"Released settled cash for {trade_day}.")

    def park_in_buffer(dollars: float, i: int, source_sym: str, reason: str):
        nonlocal settled_cash
        if dollars <= 0:
            return
        if is_crypto_symbol(source_sym):
            settled_cash = round(settled_cash + dollars, 2)
            evt(i, source_sym, "BUFFER — settled cash", None, dollars, reason)
            return
        release_day = next_settlement_day(bar_day[i], source_sym)
        unsettled_cash[release_day] = round(unsettled_cash.get(release_day, 0.0) + dollars, 2)
        evt(
            i,
            source_sym,
            "BUFFER — unsettled cash",
            None,
            dollars,
            f"{reason} Funds release on {release_day}.",
        )

    def raise_cash(required: float, i: int, reason: str):
        return

    def refill_buffer_from_cash(i: int):
        return

    def rebalance_portfolio(i: int):
        nonlocal settled_cash, turnover, rebalance_count
        total = total_value(i)
        did_trade = False

        for sym in symbols:
            if not can_trade(sym):
                continue
            price = assets[sym]["closes"][i]
            target_value = total * target_weights[sym]
            current_value = st[sym]["qty"] * price
            excess = current_value - target_value
            if excess < cfg.min_rebalance_notional:
                continue
            sell_qty = excess / price if uses_fractional_shares(sym) else math.floor(excess / price)
            if sell_qty <= 0:
                continue
            proceeds, fill_price = sell_proceeds(sym, sell_qty, price)
            if proceeds < cfg.min_order_notional:
                continue
            st[sym]["qty"] -= sell_qty
            turnover += proceeds
            mark_traded(sym)
            did_trade = True
            evt(i, sym, "REBALANCE — sold", fill_price, proceeds, "Trimmed overweight position.")
            park_in_buffer(proceeds, i, sym, "Held rebalance proceeds in cash buffer.")

        deficits: list[tuple[float, str]] = []
        for sym in symbols:
            price = assets[sym]["closes"][i]
            target_value = total * target_weights[sym]
            current_value = st[sym]["qty"] * price
            gap = target_value - current_value
            if gap > 0:
                deficits.append((gap, sym))
        deficits.sort(reverse=True)

        for _, sym in deficits:
            if not can_trade(sym):
                continue
            price = assets[sym]["closes"][i]
            target_value = total * target_weights[sym]
            current_value = st[sym]["qty"] * price
            gap = target_value - current_value
            if gap < cfg.min_rebalance_notional:
                continue
            if uses_fractional_shares(sym):
                required = gap
                raise_cash(required, i, f"Fund {display(sym)} rebalance.")
                spend = min(required, settled_cash)
                if spend < cfg.min_order_notional:
                    continue
                if is_crypto_symbol(sym):
                    qty = spend / exec_price(sym, price, "buy")
                else:
                    per_share = exec_price(sym, price, "buy") + cfg.equity_cat_per_share
                    qty = spend / per_share if per_share > 0 else 0.0
            else:
                target_shares = math.floor(target_value / (exec_price(sym, price, "buy") + cfg.equity_cat_per_share))
                current_shares = math.floor(st[sym]["qty"])
                needed = max(0, target_shares - current_shares)
                if needed <= 0:
                    continue
                required = needed * exec_price(sym, price, "buy") + equity_buy_fee(needed)
                raise_cash(required, i, f"Fund {display(sym)} rebalance.")
                affordable = min(
                    needed,
                    math.floor(settled_cash / (exec_price(sym, price, "buy") + cfg.equity_cat_per_share)),
                )
                if affordable <= 0:
                    continue
                qty = affordable
                spend = round(qty * exec_price(sym, price, "buy") + equity_buy_fee(qty), 2)
                if spend < cfg.min_order_notional:
                    continue
            st[sym]["qty"] += qty
            settled_cash = round(settled_cash - spend, 2)
            turnover += spend
            mark_traded(sym)
            did_trade = True
            evt(i, sym, "REBALANCE — bought", exec_price(sym, price, "buy"), spend, "Restored target-weight exposure.")
        if did_trade:
            rebalance_count += 1

    def snap(i: int):
        total = total_value(i)
        totals_over_time.append(total)
        if not record_events:
            return
        vals = {display(sym): round(st[sym]["qty"] * assets[sym]["closes"][i], 2) for sym in symbols}
        vals["Cash"] = round(settled_cash, 2)
        vals["Unsettled Cash"] = unsettled_total()
        history.append({"date": timestamps[i], "assets": vals, "total": total})

    def execute_pending_stop(i: int, sym: str, pending: dict[str, float | int | str | bool]):
        nonlocal turnover, stop_count
        s = st[sym]
        sell_qty = min(float(pending["qty"]), float(s["qty"]))
        pending_stops.pop(sym, None)
        if sell_qty <= 0:
            return
        stop_reference = float(pending["trigger_floor"])
        fill_reference = assets[sym]["closes"][i] if pending.get("terminal_fallback") else asset_open(sym, i)
        stop_anchor = min(stop_reference, fill_reference)
        proceeds, fill_price = sell_proceeds(sym, sell_qty, stop_anchor)
        s["qty"] -= sell_qty
        mark_traded(sym)
        fp = floor_pct(sym, i)
        tp = trigger_pct(sym, i)
        s["floor"] = round(stop_anchor * (1 - fp), 2)
        s["t_next"] = round(stop_anchor * (1 + tp), 2)
        s["stop_ready_day"] = next_trading_day(bar_day[i], cfg.stop_cooldown_days + 1)
        park_in_buffer(proceeds, i, sym, "Held for rebalance in cash buffer.")
        turnover += proceeds
        stop_count += 1
        reference_label = "close" if pending.get("terminal_fallback") else "open"
        evt(
            i,
            sym,
            "STOP — sold",
            fill_price,
            proceeds,
            f"Triggered on {pending['triggered_at']} after low ${float(pending['trigger_low']):,.2f} "
            f"breached floor ${float(pending['trigger_floor']):,.2f}; executed at next tradable "
            f"{reference_label} reference ${fill_reference:,.2f}.",
        )

    for sym in symbols:
        entry = assets[sym]["closes"][0]
        evt(0, sym, "BUY", entry, round(cfg.initial * target_weights[sym], 2), "Initial purchase.")
        snap(0)

    for i in range(1, len(timestamps)):
        traded_this_bar.clear()
        settle_cash_if_ready(i)
        for sym in symbols:
            pending = pending_stops.get(sym)
            if not pending or pending["execute_i"] != i:
                continue
            execute_pending_stop(i, sym, pending)
        if cfg.enable_risk_controls:
            for sym in symbols:
                if not tradable_on_bar(sym, i):
                    continue
                close = assets[sym]["closes"][i]
                low = assets[sym]["lows"][i]
                s = st[sym]
                if can_trade(sym) and bar_day[i] >= s["stop_ready_day"] and low <= s["floor"]:
                    sell_qty = s["qty"] * cfg.stop_sell_pct
                    if sell_qty * float(s["floor"]) < cfg.min_order_notional:
                        continue
                    execute_i = next_trade_after[sym][i]
                    terminal_fallback = execute_i is None
                    if execute_i is None:
                        execute_i = i
                    pending_stops[sym] = {
                        "qty": sell_qty,
                        "trigger_floor": s["floor"],
                        "trigger_low": low,
                        "triggered_at": timestamps[i],
                        "execute_i": execute_i,
                        "terminal_fallback": terminal_fallback,
                    }
                    exec_label = "current close" if terminal_fallback else "next tradable open"
                    evt(
                        i,
                        sym,
                        "STOP — armed",
                        s["floor"],
                        None,
                        f"Low ${low:,.2f} breached floor ${float(s['floor']):,.2f}; scheduled for {exec_label}.",
                    )
                    continue
                if close >= s["t_next"]:
                    new_floor = round(close * cfg.trail_stop, 2)
                    if new_floor > s["floor"]:
                        trail_count += 1
                        old_floor = s["floor"]
                        s["floor"] = new_floor
                        s["t_next"] = round(close * cfg.trail_step, 2)
                        evt(i, sym, "TRAIL — floor raised", close, None, f"Floor ${old_floor:,.2f} -> ${new_floor:,.2f}.")

        for sym in list(pending_stops):
            pending = pending_stops.get(sym)
            if pending and pending.get("terminal_fallback") and pending["execute_i"] == i:
                execute_pending_stop(i, sym, pending)

        if cfg.rebalance_every_bars > 0 and timestamps[i] in rebalance_timestamps:
            rebalance_portfolio(i)
        snap(i)

    init_qtys: dict[str, float] = {}
    for sym in symbols:
        qty, _ = buy_qty(sym, cfg.initial * target_weights[sym], assets[sym]["closes"][0])
        init_qtys[sym] = qty
    bh = [round(sum(init_qtys[s] * assets[s]["closes"][i] for s in symbols), 2) for i in range(len(timestamps))]
    totals = totals_over_time if totals_over_time else [cfg.initial]
    peak = totals[0]
    max_dd = 0.0
    for total in totals:
        peak = max(peak, total)
        max_dd = max(max_dd, (peak - total) / peak if peak else 0.0)

    return {
        "dates": timestamps,
        "timestamps": timestamps,
        "symbols": [display(s) for s in symbols],
        "history": history,
        "events": events,
        "summary": {
            "initial": cfg.initial,
            "final": round(totals[-1], 2),
            "return_pct": round((totals[-1] - cfg.initial) / cfg.initial * 100, 2),
            "max_dd_pct": round(max_dd * 100, 2),
            "bh_final": round(bh[-1], 2),
            "bh_return_pct": round((bh[-1] - cfg.initial) / cfg.initial * 100, 2),
            "n_stops": stop_count,
            "n_trails": trail_count,
            "n_rebalances": rebalance_count,
            "turnover": round(turnover, 2),
        },
    }
