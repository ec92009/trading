"""
Shared hourly simulator for two related strategies:

- Pure hourly rebalance
- Hourly stop/trigger + rebalance

Both use the same engine and the same stock-session hourly timestamps from
Alpaca historical bars so the comparison stays clean.
"""

from __future__ import annotations

import hashlib
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
CACHE_VERSION = 1

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
_cache_lock = Lock()


def display(sym: str) -> str:
    return DISPLAY_LABELS.get(sym, sym)


def is_fractional(sym: str) -> bool:
    return sym in FRACTIONAL_SYMBOLS


def is_absorber(sym: str) -> bool:
    return sym == ABSORBER_SYMBOL


def _utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _ts_key(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stock_session_bar(dt: datetime) -> bool:
    et = dt.astimezone(ET)
    return et.weekday() < 5 and et.hour in REGULAR_HOURLY_STARTS_ET


def _disk_cache_path(symbols: list[str], start: str, end: str) -> Path:
    cache_key = "|".join([str(CACHE_VERSION), ",".join(symbols), start, end])
    digest = hashlib.sha256(cache_key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{start}_{end}_{digest}.json"


def _chunk_ranges(start_dt: datetime, end_dt: datetime, days: int = 120) -> list[tuple[datetime, datetime]]:
    ranges: list[tuple[datetime, datetime]] = []
    cursor = start_dt
    while cursor < end_dt:
        nxt = min(end_dt, cursor + timedelta(days=days))
        ranges.append((cursor, nxt))
        cursor = nxt
    return ranges


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
    source_rows: dict[str, tuple[float, float, float]],
) -> dict[str, list[float]]:
    closes: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    first_row = source_rows[min(source_rows)] if source_rows else None
    if first_row is None:
        raise ValueError("Cannot fill empty source rows")
    last_close = None
    for ts in union:
        row = source_rows.get(ts)
        if row is not None:
            close, low, high = row
            last_close = close
        elif last_close is not None:
            close = low = high = last_close
        else:
            close = low = high = first_row[0]
        closes.append(round(close, 4))
        lows.append(round(low, 4))
        highs.append(round(high, 4))
    return {"closes": closes, "lows": lows, "highs": highs}


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
    disk_path = _disk_cache_path(symbols, start, end)
    if disk_path.exists():
        try:
            payload = json.loads(disk_path.read_text())
            with _cache_lock:
                _cache[cache_key] = payload
            return payload
        except Exception:
            pass

    stock_symbols = [sym for sym in symbols if sym != ABSORBER_SYMBOL]
    stock_fetch = stock_symbols + ["SPY"]
    start_dt = datetime.fromisoformat(f"{start}T00:00:00+00:00")
    end_dt = datetime.fromisoformat(f"{end}T00:00:00+00:00")

    raw: dict[str, dict[str, tuple[float, float, float]]] = {}
    stock_union: set[str] = set()

    for sym in stock_fetch:
        rows: dict[str, tuple[float, float, float]] = {}
        for chunk_start, chunk_end in _chunk_ranges(start_dt, end_dt):
            stock_res = _stock_data.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=sym,
                    timeframe=TimeFrame.Hour,
                    start=chunk_start,
                    end=chunk_end,
                    adjustment=Adjustment.ALL,
                )
            )
            for bar in stock_res.data.get(sym, []):
                if not _stock_session_bar(bar.timestamp):
                    continue
                rows[_ts_key(bar.timestamp)] = (float(bar.close), float(bar.low), float(bar.high))
        raw[sym] = rows
        stock_union.update(rows)

    if ABSORBER_SYMBOL in symbols:
        rows = {}
        for chunk_start, chunk_end in _chunk_ranges(start_dt, end_dt):
            crypto_res = _crypto_data.get_crypto_bars(
                CryptoBarsRequest(
                    symbol_or_symbols=ABSORBER_SYMBOL,
                    timeframe=TimeFrame.Hour,
                    start=chunk_start,
                    end=chunk_end,
                )
            )
            for bar in crypto_res.data.get(ABSORBER_SYMBOL, []):
                rows[_ts_key(bar.timestamp)] = (float(bar.close), float(bar.low), float(bar.high))
        raw[ABSORBER_SYMBOL] = rows

    union = sorted(stock_union | (set(raw[ABSORBER_SYMBOL]) if ABSORBER_SYMBOL in symbols else set()))
    stock_timestamps = sorted(stock_union)

    assets: dict[str, dict] = {}
    for sym in stock_fetch + ([ABSORBER_SYMBOL] if ABSORBER_SYMBOL in symbols else []):
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
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    disk_path.write_text(json.dumps(payload))
    with _cache_lock:
        _cache[cache_key] = payload
    return payload


@dataclass
class HourlyConfig:
    initial: float = 10_000.0
    target_weights: dict[str, float] | None = None
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

    def slippage_bps(sym: str) -> float:
        return cfg.crypto_slippage_bps if is_absorber(sym) else cfg.stock_slippage_bps

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
        if is_fractional(sym):
            qty = dollars / fill_price
            qty *= 1 - (cfg.crypto_taker_fee_bps / 10_000) if is_absorber(sym) else 1.0
            return qty, 0.0
        per_share = fill_price + cfg.equity_cat_per_share
        qty = math.floor(dollars / per_share)
        spent = qty * fill_price + equity_buy_fee(qty)
        return qty, round(dollars - spent, 2)

    def sell_proceeds(sym: str, qty: float, price: float) -> tuple[float, float]:
        fill_price = exec_price(sym, price, "sell")
        gross = qty * fill_price
        if is_absorber(sym):
            fee = gross * (cfg.crypto_taker_fee_bps / 10_000)
            return round(gross - fee, 2), fill_price
        fee = equity_sell_fee(qty, gross)
        return round(gross - fee, 2), fill_price

    st: dict[str, dict[str, float | str]] = {}
    cash = 0.0
    buffer_qty = [0.0]
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
        cash = round(cash + leftover, 2)

    if cash > 0 and ABSORBER_SYMBOL in symbols:
        qty, leftover = buy_qty(ABSORBER_SYMBOL, cash, assets[ABSORBER_SYMBOL]["closes"][0])
        buffer_qty[0] += qty
        cash = leftover

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

    def buffer_price(i: int) -> float:
        return assets[ABSORBER_SYMBOL]["closes"][i]

    def total_value(i: int) -> float:
        buffer_value = buffer_qty[0] * buffer_price(i) if ABSORBER_SYMBOL in symbols else 0.0
        return round(cash + buffer_value + sum(st[sym]["qty"] * assets[sym]["closes"][i] for sym in symbols), 2)

    traded_this_bar: set[str] = set()

    def can_trade(sym: str) -> bool:
        return sym not in traded_this_bar

    def mark_traded(sym: str):
        traded_this_bar.add(sym)

    def park_in_buffer(dollars: float, i: int, source_sym: str, reason: str):
        nonlocal cash, turnover
        if dollars <= 0:
            return
        if ABSORBER_SYMBOL in symbols and can_trade(ABSORBER_SYMBOL):
            price = buffer_price(i)
            qty, left = buy_qty(ABSORBER_SYMBOL, dollars, price)
            spent = round(dollars - left, 2)
            if qty > 0:
                buffer_qty[0] += qty
                mark_traded(ABSORBER_SYMBOL)
                turnover += spent
                evt(i, ABSORBER_SYMBOL, "BUFFER — bought", exec_price(ABSORBER_SYMBOL, price, "buy"), spent, reason)
            cash = round(cash + left, 2)
            return
        cash = round(cash + dollars, 2)

    def raise_cash(required: float, i: int, reason: str):
        nonlocal cash, turnover
        if required <= cash or ABSORBER_SYMBOL not in symbols or buffer_qty[0] <= 0 or not can_trade(ABSORBER_SYMBOL):
            return
        shortfall = required - cash
        price = buffer_price(i)
        btc_sell_qty = min(buffer_qty[0], shortfall / max(exec_price(ABSORBER_SYMBOL, price, "sell"), 1e-9))
        if btc_sell_qty <= 0:
            return
        proceeds, fill_price = sell_proceeds(ABSORBER_SYMBOL, btc_sell_qty, price)
        buffer_qty[0] -= btc_sell_qty
        cash = round(cash + proceeds, 2)
        turnover += proceeds
        mark_traded(ABSORBER_SYMBOL)
        evt(i, ABSORBER_SYMBOL, "BUFFER — sold", fill_price, proceeds, reason)

    def refill_buffer_from_cash(i: int):
        nonlocal cash, turnover
        if cash <= 0 or ABSORBER_SYMBOL not in symbols or not can_trade(ABSORBER_SYMBOL):
            return
        price = buffer_price(i)
        qty, left = buy_qty(ABSORBER_SYMBOL, cash, price)
        spent = round(cash - left, 2)
        if qty > 0:
            buffer_qty[0] += qty
            turnover += spent
            mark_traded(ABSORBER_SYMBOL)
            evt(i, ABSORBER_SYMBOL, "BUFFER — bought", exec_price(ABSORBER_SYMBOL, price, "buy"), spent, "Moved idle cash into BTC buffer.")
        cash = left

    def rebalance_portfolio(i: int):
        nonlocal cash, turnover, rebalance_count
        total = total_value(i)
        did_trade = False

        for sym in symbols:
            if not can_trade(sym):
                continue
            price = assets[sym]["closes"][i]
            target_value = total * target_weights[sym]
            current_value = st[sym]["qty"] * price
            if is_absorber(sym):
                current_value = max(0.0, st[sym]["qty"] - buffer_qty[0]) * price
            excess = current_value - target_value
            if excess <= 0:
                continue
            if is_absorber(sym):
                core_qty = max(0.0, st[sym]["qty"] - buffer_qty[0])
                sell_qty = min(core_qty, excess / price)
                if sell_qty <= 0:
                    continue
                st[sym]["qty"] -= sell_qty
                buffer_qty[0] += sell_qty
                mark_traded(sym)
                did_trade = True
                evt(i, sym, "REBALANCE — sold", exec_price(sym, price, "sell"), excess, "Moved BTC core into BTC buffer.")
                continue
            sell_qty = excess / price if is_fractional(sym) else math.floor(excess / price)
            if sell_qty <= 0:
                continue
            proceeds, fill_price = sell_proceeds(sym, sell_qty, price)
            st[sym]["qty"] -= sell_qty
            turnover += proceeds
            mark_traded(sym)
            did_trade = True
            evt(i, sym, "REBALANCE — sold", fill_price, proceeds, "Trimmed overweight position.")
            park_in_buffer(proceeds, i, sym, "Parked rebalance proceeds in BTC buffer.")

        deficits: list[tuple[float, str]] = []
        for sym in symbols:
            price = assets[sym]["closes"][i]
            target_value = total * target_weights[sym]
            current_value = st[sym]["qty"] * price
            if is_absorber(sym):
                current_value = max(0.0, st[sym]["qty"] - buffer_qty[0]) * price
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
            if is_absorber(sym):
                current_value = max(0.0, st[sym]["qty"] - buffer_qty[0]) * price
            gap = target_value - current_value
            if gap <= 0:
                continue
            if is_absorber(sym):
                needed_qty = gap / price
                moved_qty = min(buffer_qty[0], needed_qty)
                if moved_qty > 0:
                    moved_value = round(moved_qty * price, 2)
                    buffer_qty[0] -= moved_qty
                    st[sym]["qty"] += moved_qty
                    mark_traded(sym)
                    did_trade = True
                    evt(i, sym, "REBALANCE — bought", exec_price(sym, price, "buy"), moved_value, "Moved BTC buffer back toward target BTC weight.")
                remaining_cost = round(max(0.0, gap - moved_qty * price), 2)
                if remaining_cost <= 0:
                    continue
                raise_cash(remaining_cost, i, "Fund BTC rebalance.")
                spend = min(remaining_cost, cash)
                if spend <= 0:
                    continue
                qty = (spend / exec_price(sym, price, "buy")) * (1 - cfg.crypto_taker_fee_bps / 10_000)
                st[sym]["qty"] += qty
                cash = round(cash - spend, 2)
                turnover += spend
                mark_traded(sym)
                did_trade = True
                evt(i, sym, "REBALANCE — bought", exec_price(sym, price, "buy"), spend, "Restored target BTC exposure.")
                continue
            if is_fractional(sym):
                required = gap
                raise_cash(required, i, f"Fund {display(sym)} rebalance.")
                spend = min(required, cash)
                if spend <= 0:
                    continue
                qty = spend / exec_price(sym, price, "buy")
            else:
                target_shares = math.floor(target_value / (exec_price(sym, price, "buy") + cfg.equity_cat_per_share))
                current_shares = math.floor(st[sym]["qty"])
                needed = max(0, target_shares - current_shares)
                if needed <= 0:
                    continue
                required = needed * exec_price(sym, price, "buy") + equity_buy_fee(needed)
                raise_cash(required, i, f"Fund {display(sym)} rebalance.")
                affordable = min(needed, math.floor(cash / (exec_price(sym, price, "buy") + cfg.equity_cat_per_share)))
                if affordable <= 0:
                    continue
                qty = affordable
                spend = round(qty * exec_price(sym, price, "buy") + equity_buy_fee(qty), 2)
            st[sym]["qty"] += qty
            cash = round(cash - spend, 2)
            turnover += spend
            mark_traded(sym)
            did_trade = True
            evt(i, sym, "REBALANCE — bought", exec_price(sym, price, "buy"), spend, "Restored target-weight exposure.")

        refill_buffer_from_cash(i)
        if did_trade:
            rebalance_count += 1

    def snap(i: int):
        total = total_value(i)
        totals_over_time.append(total)
        if not record_events:
            return
        vals = {display(sym): round(st[sym]["qty"] * assets[sym]["closes"][i], 2) for sym in symbols}
        if ABSORBER_SYMBOL in symbols and buffer_qty[0] > 0:
            vals["BTC Buffer"] = round(buffer_qty[0] * assets[ABSORBER_SYMBOL]["closes"][i], 2)
        vals["Cash"] = round(cash, 2)
        history.append({"date": timestamps[i], "assets": vals, "total": total})

    for sym in symbols:
        entry = assets[sym]["closes"][0]
        evt(0, sym, "BUY", entry, round(cfg.initial * target_weights[sym], 2), "Initial purchase.")
    snap(0)

    for i in range(1, len(timestamps)):
        traded_this_bar.clear()
        if cfg.enable_risk_controls:
            for sym in symbols:
                if sym != ABSORBER_SYMBOL and timestamps[i] not in stock_timestamps:
                    continue
                close = assets[sym]["closes"][i]
                low = assets[sym]["lows"][i]
                s = st[sym]
                if can_trade(sym) and bar_day[i] >= s["stop_ready_day"] and low <= s["floor"]:
                    stop_count += 1
                    sp = s["floor"]
                    sell_qty = s["qty"] * cfg.stop_sell_pct
                    proceeds, fill_price = sell_proceeds(sym, sell_qty, sp)
                    s["qty"] -= sell_qty
                    mark_traded(sym)
                    fp = floor_pct(sym, i)
                    tp = trigger_pct(sym, i)
                    old_floor = s["floor"]
                    s["floor"] = round(sp * (1 - fp), 2)
                    s["t_next"] = round(sp * (1 + tp), 2)
                    s["stop_ready_day"] = next_trading_day(bar_day[i], cfg.stop_cooldown_days + 1)
                    if is_absorber(sym):
                        buffer_qty[0] += sell_qty
                    else:
                        park_in_buffer(proceeds, i, sym, "Held for rebalance after stop.")
                    turnover += proceeds
                    evt(i, sym, "STOP — sold", fill_price, proceeds, f"Low ${low:,.2f} hit floor ${old_floor:,.2f}.")
                    continue
                if close >= s["t_next"]:
                    new_floor = round(close * cfg.trail_stop, 2)
                    if new_floor > s["floor"]:
                        trail_count += 1
                        old_floor = s["floor"]
                        s["floor"] = new_floor
                        s["t_next"] = round(close * cfg.trail_step, 2)
                        evt(i, sym, "TRAIL — floor raised", close, None, f"Floor ${old_floor:,.2f} -> ${new_floor:,.2f}.")

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
