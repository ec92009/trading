"""
Trading algo sandbox — multi-asset 1-year back-test with beta-calibrated floors.

Usage:
    python3 sim.py
    python3 sim.py --no-browser --port 8092
"""
from __future__ import annotations

import json
import math
import sys
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yfinance as yf

PORT = 8092
HERE = Path(__file__).parent
VERSION_PATH = HERE / "VERSION"

SYMBOLS = ["TSLA", "TSM", "NVDA", "PLTR", "AAPL", "GOOGL", "META", "AMZN", "MSFT", "BTC-USD"]
LABELS  = {"TSM": "TSMC", "GOOGL": "Alphabet", "BTC-USD": "BTC"}
COLORS  = ["#d8b27a", "#71d6ad", "#73b7ff", "#c4a7ff", "#ff8f70", "#f4d35e", "#ef6f6c", "#5ec2b7", "#7aa6ff", "#8ab4f8"]
BETA_WINDOW = 60
COLOR_BY_SYMBOL = dict(zip(SYMBOLS, COLORS))
CASH_COLOR = "#f1d48a"
ABSORBER_SYMBOL = "BTC-USD"
BUFFER_LABEL = "BTC Buffer"
BUFFER_COLOR = "#5b8def"
FRACTIONAL_SYMBOLS = {ABSORBER_SYMBOL}

_cache: dict[tuple[str | None, str | None, str | None], dict] = {}
_cache_lock = threading.Lock()


def display(sym: str) -> str:
    return LABELS.get(sym, sym)


RAW_BY_DISPLAY = {display(sym): sym for sym in SYMBOLS}


def normalize_symbols(items: list[str] | None) -> list[str]:
    if not items:
        return SYMBOLS[:]
    chosen: list[str] = []
    for item in items:
        raw = RAW_BY_DISPLAY.get(item, item)
        if raw in SYMBOLS and raw not in chosen:
            chosen.append(raw)
    return chosen or SYMBOLS[:]


def is_fractional(sym: str) -> bool:
    return sym in FRACTIONAL_SYMBOLS


def is_absorber(sym: str) -> bool:
    return sym == ABSORBER_SYMBOL


def read_version() -> str:
    if VERSION_PATH.exists():
        v = VERSION_PATH.read_text().strip()
        return v if v.startswith("v") else f"v{v}"
    return "v0.0"


# ── Beta computation ───────────────────────────────────────────────────────────

def _compute_rolling_betas(assets: dict, n: int) -> dict[str, list[float]]:
    """60-day rolling beta vs SPY for each traded symbol. Clamped to [0.3, 4.0]."""
    spy_c = assets["SPY"]["closes"]
    spy_r = [spy_c[i] / spy_c[i - 1] - 1 for i in range(1, n)]

    result: dict[str, list[float]] = {}
    for sym in SYMBOLS:
        c = assets[sym]["closes"]
        ar = [c[i] / c[i - 1] - 1 for i in range(1, n)]
        series: list[float | None] = [None]          # day 0 has no prior return
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
        # forward-fill Nones with first valid value
        first = next((b for b in series if b is not None), 1.0)
        result[sym] = [b if b is not None else first for b in series]
    return result


# ── Data ──────────────────────────────────────────────────────────────────────

def load_data(*, period: str = "1y", start: str | None = None, end: str | None = None) -> dict:
    """Download (or return cached) OHLCV for all assets + SPY over a chosen window."""
    cache_key = (period, start, end)
    with _cache_lock:
        if cache_key in _cache:
            return _cache[cache_key]
        fetch_syms = SYMBOLS + ["SPY"]
        dfs = {}
        for sym in fetch_syms:
            history_kwargs = {"start": start, "end": end} if start or end else {"period": period}
            df = yf.Ticker(sym).history(**history_kwargs)[["Close", "Low", "High"]].dropna()
            df.index = [d.date() for d in df.index]
            dfs[sym] = df
        common = sorted(set.intersection(*[set(df.index) for df in dfs.values()]))
        n = len(common)
        assets: dict[str, dict] = {}
        for sym in fetch_syms:
            df = dfs[sym].loc[common]
            assets[sym] = {
                "closes": [round(float(v), 4) for v in df["Close"]],
                "lows":   [round(float(v), 4) for v in df["Low"]],
                "highs":  [round(float(v), 4) for v in df["High"]],
            }
        betas = _compute_rolling_betas(assets, n)
        avg_betas = {
            display(sym): round(sum(betas[sym]) / n, 2)
            for sym in SYMBOLS
        }
        payload = {
            "dates":     [str(d) for d in common],
            "assets":    assets,
            "betas":     betas,
            "avg_betas": avg_betas,
        }
        _cache[cache_key] = payload
        return payload


# ── Simulation ────────────────────────────────────────────────────────────────

@dataclass
class SimConfig:
    initial:       float = 1000.0
    base_tol:      float = 0.05   # floor distance = base_tol × β (e.g. 5% × 2 = 10%)
    trail_step:    float = 1.05
    trail_stop:    float = 0.95
    stop_sell_pct: float = 0.05
    stop_cooldown_days: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "SimConfig":
        valid = set(cls.__dataclass_fields__)
        return cls(**{k: float(v) for k, v in d.items() if k in valid})


def simulate(cfg: SimConfig, data: dict, chosen_symbols: list[str] | None = None) -> dict:
    chosen_symbols = normalize_symbols(chosen_symbols)
    dates  = data["dates"]
    adata  = data["assets"]
    betas  = data["betas"]
    n      = len(dates)
    per    = cfg.initial / len(chosen_symbols)

    def floor_pct(sym: str, i: int) -> float:
        """Stop floor: base_tol × β below current price."""
        return max(0.005, cfg.base_tol * betas[sym][i])

    def trigger_pct(sym: str, i: int) -> float:
        """Trail trigger: base_tol × β above current price."""
        return max(0.005, cfg.base_tol * betas[sym][i])

    def buy_qty(sym: str, dollars: float, price: float) -> tuple[float, float]:
        if dollars <= 0:
            return 0.0, 0.0
        if is_fractional(sym):
            qty = dollars / price
            spent = dollars
        else:
            qty = math.floor(dollars / price)
            spent = qty * price
        return qty, round(dollars - spent, 2)

    # Per-asset state
    st: dict[str, dict] = {}
    cash_balance = 0.0
    buffer_qty = [0.0]
    for sym in chosen_symbols:
        entry = adata[sym]["closes"][0]
        fp    = floor_pct(sym, 0)
        tp    = trigger_pct(sym, 0)
        qty, leftover = buy_qty(sym, per, entry)
        st[sym] = {
            "qty":    qty,
            "floor":  round(entry * (1 - fp), 2),
            "t_next": round(entry * (1 + tp), 2),
            "stop_ready_day": 1,
        }
        cash_balance = round(cash_balance + leftover, 2)

    if cash_balance > 0 and ABSORBER_SYMBOL in chosen_symbols:
        absorber_price = adata[ABSORBER_SYMBOL]["closes"][0]
        qty, leftover = buy_qty(ABSORBER_SYMBOL, cash_balance, absorber_price)
        buffer_qty[0] += qty
        cash_balance = leftover

    events:  list[dict] = []
    history: list[dict] = []

    def evt(i, sym, action, price, amount, reason):
        events.append({
            "date":   dates[i],
            "symbol": display(sym) if sym in SYMBOLS else sym,
            "action": action,
            "price":  round(price, 2)  if price  is not None else None,
            "amount": round(amount, 2) if amount is not None else None,
            "reason": reason,
        })

    def buffer_price(i: int) -> float:
        return adata[ABSORBER_SYMBOL]["closes"][i]

    def total_value(i: int) -> float:
        buffer_value = buffer_qty[0] * buffer_price(i) if ABSORBER_SYMBOL in chosen_symbols else 0.0
        return round(
            cash_balance + buffer_value + sum(st[sym]["qty"] * adata[sym]["closes"][i] for sym in chosen_symbols),
            2,
        )

    traded_today: set[str] = set()

    def can_trade(sym: str) -> bool:
        return sym not in traded_today

    def mark_traded(sym: str):
        traded_today.add(sym)

    def park_in_buffer(dollars: float, i: int, source_sym: str, action: str, reason: str):
        nonlocal cash_balance
        if dollars <= 0:
            return
        if ABSORBER_SYMBOL in chosen_symbols and can_trade(ABSORBER_SYMBOL):
            price = buffer_price(i)
            qty, left = buy_qty(ABSORBER_SYMBOL, dollars, price)
            if qty > 0:
                buffer_qty[0] += qty
                mark_traded(ABSORBER_SYMBOL)
                evt(i, BUFFER_LABEL, action, price, round(dollars - left, 2),
                    f"${dollars - left:,.2f} from {display(source_sym)} → "
                    f"{qty:.4f} sh of {BUFFER_LABEL} at ${price:,.2f}. {reason}")
            cash_balance = round(cash_balance + left, 2)
            return
        cash_balance = round(cash_balance + dollars, 2)

    def raise_cash(required: float, i: int, reason: str):
        nonlocal cash_balance
        if (
            required <= cash_balance
            or ABSORBER_SYMBOL not in chosen_symbols
            or buffer_qty[0] <= 0
            or not can_trade(ABSORBER_SYMBOL)
        ):
            return
        shortfall = required - cash_balance
        price = buffer_price(i)
        btc_sell_qty = min(buffer_qty[0], shortfall / price)
        if btc_sell_qty <= 0:
            return
        proceeds = round(btc_sell_qty * price, 2)
        buffer_qty[0] -= btc_sell_qty
        cash_balance = round(cash_balance + proceeds, 2)
        mark_traded(ABSORBER_SYMBOL)
        evt(i, BUFFER_LABEL, "REBALANCE — sold", price, proceeds,
            f"Sold {btc_sell_qty:.4f} sh of {BUFFER_LABEL} at ${price:,.2f} to {reason}")

    def refill_buffer_from_cash(i: int):
        nonlocal cash_balance
        if cash_balance <= 0 or ABSORBER_SYMBOL not in chosen_symbols or not can_trade(ABSORBER_SYMBOL):
            return
        price = buffer_price(i)
        qty, left = buy_qty(ABSORBER_SYMBOL, cash_balance, price)
        spent = round(cash_balance - left, 2)
        if qty > 0:
            buffer_qty[0] += qty
            mark_traded(ABSORBER_SYMBOL)
            evt(i, BUFFER_LABEL, "REBALANCE — bought", price, spent,
                f"Moved idle cash into {BUFFER_LABEL}: {qty:.4f} sh at ${price:,.2f}.")
        cash_balance = left

    def rebalance_portfolio(i: int):
        nonlocal cash_balance
        if not chosen_symbols:
            return
        target_value = total_value(i) / len(chosen_symbols)

        # Trim overweight positions first so underweights can be funded immediately.
        for sym in chosen_symbols:
            if not can_trade(sym):
                continue
            price = adata[sym]["closes"][i]
            current_value = st[sym]["qty"] * price
            excess = current_value - target_value
            if excess <= 0:
                continue
            if is_absorber(sym):
                sell_qty = excess / price
                if sell_qty <= 0:
                    continue
                st[sym]["qty"] -= sell_qty
                buffer_qty[0] += sell_qty
                mark_traded(sym)
                evt(i, sym, "REBALANCE — sold", price, excess,
                    f"Reduced {display(sym)} by {sell_qty:.4f} sh at ${price:,.2f} "
                    f"to move toward equal-weight target (${target_value:,.2f}).")
                evt(i, BUFFER_LABEL, "REBALANCE — bought", price, excess,
                    f"Shifted {sell_qty:.4f} sh from BTC core into {BUFFER_LABEL}.")
                continue
            sell_qty = excess / price if is_fractional(sym) else math.floor(excess / price)
            if sell_qty <= 0:
                continue
            proceeds = round(sell_qty * price, 2)
            st[sym]["qty"] -= sell_qty
            mark_traded(sym)
            evt(i, sym, "REBALANCE — sold", price, proceeds,
                f"Reduced {display(sym)} by {sell_qty:.4f} sh at ${price:,.2f} "
                f"to move toward equal-weight target (${target_value:,.2f}).")
            park_in_buffer(proceeds, i, sym, "REBALANCE — bought", "Held for rebalance buys.")

        deficits: list[tuple[float, str]] = []
        for sym in chosen_symbols:
            price = adata[sym]["closes"][i]
            gap = target_value - (st[sym]["qty"] * price)
            if gap > 0:
                deficits.append((gap, sym))
        deficits.sort(reverse=True)

        for _, sym in deficits:
            if not can_trade(sym):
                continue
            price = adata[sym]["closes"][i]
            current_value = st[sym]["qty"] * price
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
                    evt(i, sym, "REBALANCE — bought", price, moved_value,
                        f"Moved {moved_qty:.4f} sh from {BUFFER_LABEL} back into BTC core.")
                remaining_cost = round(max(0.0, gap - moved_qty * price), 2)
                if remaining_cost <= 0:
                    continue
                raise_cash(remaining_cost, i, f"fund BTC core rebalance for {display(sym)}.")
                spend = min(remaining_cost, cash_balance)
                if spend <= 0:
                    continue
                qty = spend / price
                st[sym]["qty"] += qty
                cash_balance = round(cash_balance - spend, 2)
                mark_traded(sym)
                evt(i, sym, "REBALANCE — bought", price, spend,
                    f"Bought {qty:.4f} sh of {display(sym)} at ${price:,.2f} "
                    f"to restore equal-weight exposure.")
                continue
            if is_fractional(sym):
                required = gap
                raise_cash(required, i, f"fund {display(sym)} rebalance buys.")
                spend = min(required, cash_balance)
                if spend <= 0:
                    continue
                qty = spend / price
                st[sym]["qty"] += qty
            else:
                target_shares = math.floor(target_value / price)
                current_shares = math.floor(st[sym]["qty"])
                needed = max(0, target_shares - current_shares)
                if needed <= 0:
                    continue
                required = needed * price
                raise_cash(required, i, f"fund {display(sym)} rebalance buys.")
                affordable = min(needed, math.floor(cash_balance / price))
                if affordable <= 0:
                    continue
                spend = round(affordable * price, 2)
                qty = affordable
                st[sym]["qty"] += affordable
            cash_balance = round(cash_balance - spend, 2)
            mark_traded(sym)
            evt(i, sym, "REBALANCE — bought", price, spend,
                f"Bought {qty:.4f} sh of {display(sym)} at ${price:,.2f} "
                f"to restore equal-weight exposure.")

        refill_buffer_from_cash(i)

    def snap(i):
        vals = {display(sym): round(st[sym]["qty"] * adata[sym]["closes"][i], 2)
                for sym in chosen_symbols}
        if ABSORBER_SYMBOL in chosen_symbols and buffer_qty[0] > 0:
            vals[BUFFER_LABEL] = round(buffer_qty[0] * adata[ABSORBER_SYMBOL]["closes"][i], 2)
        vals["Cash"] = round(cash_balance, 2)
        history.append({"date": dates[i], "assets": vals,
                         "total": round(sum(vals.values()), 2)})

    # Day 0 — initial buys
    for sym in chosen_symbols:
        entry = adata[sym]["closes"][0]
        s     = st[sym]
        b     = betas[sym][0]
        fp    = floor_pct(sym, 0)
        tp    = trigger_pct(sym, 0)
        evt(0, sym, "BUY", entry, per,
            f"Initial purchase: {s['qty']:.4f} shares at ${entry:,.2f}. "
            f"β={b:.2f} → floor {fp*100:.1f}% below = ${s['floor']:,.2f}; "
            f"trail triggers {tp*100:.1f}% above = ${s['t_next']:,.2f}.")
    snap(0)

    for i in range(1, n):
        traded_today.clear()
        for sym in chosen_symbols:
            close = adata[sym]["closes"][i]
            low   = adata[sym]["lows"][i]
            s     = st[sym]

            # ── Stop ──────────────────────────────────────────────────────────
            if can_trade(sym) and i >= s["stop_ready_day"] and low <= s["floor"]:
                sp       = s["floor"]
                sell_qty = s["qty"] * cfg.stop_sell_pct
                proceeds = round(sell_qty * sp, 2)
                s["qty"] -= sell_qty
                mark_traded(sym)
                old_floor  = s["floor"]
                fp = floor_pct(sym, i)
                tp = trigger_pct(sym, i)
                s["floor"]  = round(sp * (1 - fp), 2)
                s["t_next"] = round(sp * (1 + tp), 2)
                s["stop_ready_day"] = i + int(cfg.stop_cooldown_days) + 1
                b = betas[sym][i]
                if is_absorber(sym):
                    buffer_qty[0] += sell_qty
                    evt(i, BUFFER_LABEL, "STOP — parked", sp, proceeds,
                        f"Moved {sell_qty:.4f} sh from BTC core into {BUFFER_LABEL} after stop event.")
                    destination = BUFFER_LABEL
                else:
                    park_in_buffer(proceeds, i, sym, "STOP — parked", "Held for end-of-cycle rebalance.")
                    destination = BUFFER_LABEL if ABSORBER_SYMBOL in chosen_symbols else "cash"
                evt(i, sym, f"STOP — sold {int(cfg.stop_sell_pct*100)}%", sp, proceeds,
                    f"{display(sym)} low ${low:,.2f} hit floor ${old_floor:,.2f}. "
                    f"Sold {int(cfg.stop_sell_pct*100)}% ({sell_qty:.4f} sh) → ${proceeds:,.2f}. "
                    f"β={b:.2f}: new floor ${s['floor']:,.2f} ({fp*100:.1f}% below), "
                    f"trail at ${s['t_next']:,.2f}. Proceeds moved to {destination} until rebalance. "
                    f"Next stop eligible in {int(cfg.stop_cooldown_days)} trading day(s).")
                continue

            # ── Trail ─────────────────────────────────────────────────────────
            if close >= s["t_next"]:
                new_floor = round(close * cfg.trail_stop, 2)
                if new_floor > s["floor"]:
                    old_f      = s["floor"]
                    old_t      = s["t_next"]
                    s["floor"]  = new_floor
                    s["t_next"] = round(close * cfg.trail_step, 2)
                    evt(i, sym, "TRAIL — floor raised", close, None,
                        f"{display(sym)} ${close:,.2f} cleared trail (${old_t:,.2f}). "
                        f"Floor raised ${old_f:,.2f} → ${new_floor:,.2f}. "
                        f"Next trail at ${s['t_next']:,.2f}.")

        rebalance_portfolio(i)
        snap(i)

    # ── Buy-and-hold baseline ─────────────────────────────────────────────────
    init_qtys = {sym: per / adata[sym]["closes"][0] for sym in chosen_symbols}
    bh = [round(sum(init_qtys[s] * adata[s]["closes"][i] for s in chosen_symbols), 2)
          for i in range(n)]

    # ── Normalized prices (day-0 = 100) ──────────────────────────────────────
    prices = {}
    for sym in chosen_symbols:
        c0 = adata[sym]["closes"][0]
        prices[display(sym)] = [round(adata[sym]["closes"][i] / c0 * 100, 2)
                                 for i in range(n)]

    # ── Summary ───────────────────────────────────────────────────────────────
    totals = [h["total"] for h in history]
    peak = totals[0]; max_dd = 0.0
    for t in totals:
        peak   = max(peak, t)
        max_dd = max(max_dd, (peak - t) / peak)

    return {
        "dates":     dates,
        "symbols":   [display(s) for s in chosen_symbols],
        "all_symbols": [display(s) for s in SYMBOLS],
        "colors":    [COLOR_BY_SYMBOL[s] for s in chosen_symbols],
        "stack_symbols": [display(s) for s in chosen_symbols]
                         + ([BUFFER_LABEL] if any(h["assets"].get(BUFFER_LABEL, 0) > 0 for h in history) else [])
                         + (["Cash"] if any(h["assets"].get("Cash", 0) > 0 for h in history) else []),
        "stack_colors": [COLOR_BY_SYMBOL[s] for s in chosen_symbols]
                        + ([BUFFER_COLOR] if any(h["assets"].get(BUFFER_LABEL, 0) > 0 for h in history) else [])
                        + ([CASH_COLOR] if any(h["assets"].get("Cash", 0) > 0 for h in history) else []),
        "prices":    prices,
        "history":   history,
        "bh":        bh,
        "bh_per_asset": {
            display(sym): [round(init_qtys[sym] * adata[sym]["closes"][i], 2)
                           for i in range(n)]
            for sym in chosen_symbols
        },
        "events":    events,
        "avg_betas": {display(sym): data["avg_betas"][display(sym)] for sym in chosen_symbols},
        "summary": {
            "initial":       cfg.initial,
            "final":         round(totals[-1], 2),
            "return_pct":    round((totals[-1] - cfg.initial) / cfg.initial * 100, 2),
            "max_dd_pct":    round(max_dd * 100, 2),
            "bh_final":      round(bh[-1], 2),
            "bh_return_pct": round((bh[-1] - cfg.initial) / cfg.initial * 100, 2),
            "n_stops":  sum(1 for e in events if "STOP"  in e["action"]),
            "n_trails": sum(1 for e in events if "TRAIL" in e["action"]),
        },
    }


# ── HTML ──────────────────────────────────────────────────────────────────────

def build_html() -> str:
    ver = read_version()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Portfolio Algo Sandbox</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
  <style>
    :root {{
      --bg:#111417; --line:rgba(216,178,122,.18); --accent:#d8b27a;
      --text:#edf0eb; --muted:#93a09f; --good:#71d6ad; --bad:#ff8f70;
      --shadow:0 30px 60px rgba(0,0,0,.28);
    }}
    *{{box-sizing:border-box;}}
    html,body{{margin:0;min-height:100%;background:radial-gradient(circle at top left,rgba(216,178,122,.08),transparent 32%),linear-gradient(180deg,#0d1012 0%,#12171a 100%);color:var(--text);font-family:"Space Grotesk",sans-serif;overflow-x:hidden;}}
    body{{padding:24px;}}
    button,input,select{{font:inherit;}}
    .shell{{width:min(100%,1440px);margin:0 auto;display:grid;grid-template-columns:310px minmax(0,1fr);gap:20px;}}
    .topbar{{grid-column:1/-1;display:flex;justify-content:space-between;align-items:center;padding-bottom:12px;border-bottom:1px solid var(--line);margin-bottom:4px;}}
    .brand{{color:var(--accent);text-transform:uppercase;letter-spacing:.18em;font-size:13px;font-weight:700;}}
    .pill{{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);padding:7px 14px;border-radius:999px;color:var(--muted);font-size:13px;background:rgba(255,255,255,.02);}}
    .sidebar{{display:grid;align-content:start;gap:0;}}
    .panel{{background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,.01));border:1px solid rgba(255,255,255,.06);border-radius:24px;padding:20px;box-shadow:var(--shadow);}}
    .panel h2{{margin:0 0 18px;font-size:15px;letter-spacing:.01em;}}
    .ctrl{{display:grid;gap:18px;}}
    .ctrl-group{{display:grid;gap:5px;}}
    .ctrl-head{{display:flex;justify-content:space-between;align-items:baseline;}}
    .ctrl-name{{font-size:13px;font-weight:600;}}
    .ctrl-val{{font-family:"IBM Plex Mono",monospace;font-size:13px;color:var(--accent);}}
    .ctrl-desc{{font-size:11px;color:var(--muted);line-height:1.5;margin-top:2px;}}
    input[type=range]{{width:100%;accent-color:var(--accent);cursor:pointer;margin:4px 0;}}
    hr.div{{border:0;border-top:1px solid rgba(255,255,255,.06);margin:4px 0;}}
    .run-btn{{width:100%;border:0;border-radius:999px;padding:14px;cursor:pointer;background:var(--accent);color:#171311;font-weight:700;font-size:15px;transition:.18s;margin-top:4px;}}
    .run-btn:hover{{transform:translateY(-1px);opacity:.92;}}
    .run-btn:disabled{{opacity:.45;cursor:not-allowed;transform:none;}}
    .workspace{{display:grid;gap:18px;}}
    .stat-band{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;}}
    .stat{{padding:16px 18px;border-radius:20px;background:linear-gradient(90deg,rgba(255,255,255,.03),rgba(255,255,255,.01));border:1px solid rgba(255,255,255,.06);}}
    .stat-label{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.12em;margin-bottom:10px;}}
    .stat-value{{font-size:28px;line-height:1;letter-spacing:-.03em;font-weight:700;}}
    .stat-sub{{font-size:12px;color:var(--muted);margin-top:4px;}}
    /* Beta band */
    .beta-band{{display:flex;gap:10px;flex-wrap:wrap;padding:14px 18px;border-radius:20px;background:linear-gradient(90deg,rgba(255,255,255,.02),rgba(255,255,255,.01));border:1px solid rgba(255,255,255,.05);align-items:center;}}
    .beta-band-label{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-right:4px;}}
    .beta-chip{{display:inline-flex;gap:6px;align-items:center;padding:5px 11px;border-radius:999px;border:1px solid rgba(255,255,255,.08);font-size:12px;background:rgba(255,255,255,.03);}}
    .beta-chip .sym{{font-weight:600;font-size:11px;}}
    .beta-chip .bval{{font-family:"IBM Plex Mono",monospace;color:var(--accent);font-size:12px;}}
    .beta-chip .floor-hint{{font-size:10px;color:var(--muted);}}
    .asset-toggles{{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px;}}
    .asset-toggle{{border:1px solid rgba(255,255,255,.08);border-radius:999px;padding:6px 14px;font-size:12px;font-weight:600;cursor:pointer;background:rgba(255,255,255,.02);color:var(--muted);transition:.15s;}}
    .asset-toggle:hover{{border-color:rgba(255,255,255,.2);color:var(--text);}}
    .asset-toggle.active{{color:#171311;border-color:transparent;}}
    .asset-toggle.off{{opacity:.45;background:transparent !important;color:var(--muted);border-color:rgba(255,255,255,.08);}}
    /* Charts */
    .chart-panel{{padding:20px;border-radius:24px;background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,.01));border:1px solid rgba(255,255,255,.06);}}
    .chart-panel h3{{margin:0 0 4px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;}}
    .chart-sub{{font-size:11px;color:rgba(147,160,159,.6);margin-bottom:14px;font-family:"IBM Plex Mono",monospace;}}
    .chart-wrap{{position:relative;height:280px;}}
    /* Events */
    .events-panel{{padding:20px;border-radius:24px;background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,.01));border:1px solid rgba(255,255,255,.06);}}
    .events-panel h3{{margin:0 0 12px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;}}
    .tbl-wrap{{border-radius:14px;border:1px solid rgba(255,255,255,.05);overflow:hidden;}}
    table{{width:100%;border-collapse:collapse;font-size:12px;}}
    th,td{{padding:10px 14px;text-align:left;border-bottom:1px solid rgba(255,255,255,.04);}}
    th{{color:var(--muted);text-transform:uppercase;letter-spacing:.1em;font-size:11px;background:#13181b;}}
    tr:last-child td{{border-bottom:0;}}
    td.mono{{font-family:"IBM Plex Mono",monospace;}}
    td.reason{{color:var(--muted);font-size:11px;line-height:1.55;max-width:480px;}}
    .badge{{display:inline-flex;padding:3px 9px;border-radius:999px;font-size:11px;font-weight:600;white-space:nowrap;}}
    .badge.buy    {{color:var(--good);border:1px solid rgba(113,214,173,.35);background:rgba(113,214,173,.08);}}
    .badge.stop   {{color:var(--bad); border:1px solid rgba(255,143,112,.35);background:rgba(255,143,112,.08);}}
    .badge.trail  {{color:#73b7ff;   border:1px solid rgba(115,183,255,.35);background:rgba(115,183,255,.08);}}
    .badge.realloc{{color:#c4a7ff;   border:1px solid rgba(196,167,255,.35);background:rgba(196,167,255,.08);}}
    .sym-chip{{display:inline-block;padding:2px 7px;border-radius:6px;font-size:11px;font-weight:600;font-family:"IBM Plex Mono",monospace;background:rgba(255,255,255,.06);color:var(--muted);}}
    .filter-chips{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;}}
    .fchip{{border:1px solid rgba(255,255,255,.08);border-radius:999px;padding:5px 14px;font-size:12px;font-weight:600;cursor:pointer;background:rgba(255,255,255,.02);color:var(--muted);transition:.15s;}}
    .fchip:hover{{border-color:rgba(255,255,255,.2);color:var(--text);}}
    .fchip.active{{background:var(--accent);color:#171311;border-color:var(--accent);}}
    .empty{{color:var(--muted);padding:40px;text-align:center;}}
    @media(max-width:900px){{.shell{{grid-template-columns:1fr}}.stat-band{{grid-template-columns:repeat(2,1fr)}}}}
  </style>
</head>
<body>
<div class="shell">

  <div class="topbar">
    <div class="brand">Portfolio · Algo Sandbox</div>
    <div style="display:flex;gap:10px">
      <div class="pill" id="dataStatus">Fetching market data…</div>
      <div class="pill">{ver}</div>
    </div>
  </div>

  <!-- ── Sidebar ─────────────────────────────────────────────────────────── -->
  <aside class="sidebar">
    <div class="panel">
      <h2>Simulation Parameters</h2>
      <div class="ctrl">

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Starting portfolio</span><span class="ctrl-val" id="v_initial">$10,000</span></div>
          <input type="range" id="s_initial" min="500" max="10000" step="500" value="10000">
          <div class="ctrl-desc">Split evenly across TSLA · TSMC · NVDA · PLTR · AAPL · Alphabet · META · AMZN · MSFT · BTC on day one ($1,000 each at $10k).</div>
        </div>

        <hr class="div">

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Base tolerance</span><span class="ctrl-val" id="v_base_tol">0.5%</span></div>
          <input type="range" id="s_base_tol" min="0.5" max="15" step="0.1" value="0.5">
          <div class="ctrl-desc">Multiplied by each asset's rolling β to set its floor and trail trigger. A 5% base with TSLA β≈2 gives a 10% floor. With TSM β≈0.8, only 4% — each asset's tolerance matches its real volatility.</div>
        </div>

        <hr class="div">

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Stop sell amount</span><span class="ctrl-val" id="v_stop_sell_pct">55%</span></div>
          <input type="range" id="s_stop_sell_pct" min="1" max="75" step="1" value="55">
          <div class="ctrl-desc">On each stop, sell this fraction of the position. Proceeds go to the other four assets weighted by their current market value.</div>
        </div>

        <hr class="div">

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Trail step</span><span class="ctrl-val" id="v_trail_step">+11.9%</span></div>
          <input type="range" id="s_trail_step" min="100.5" max="130" step="0.1" value="111.9">
          <div class="ctrl-desc">After trailing starts, every X% gain steps the floor up again, locking in more profit.</div>
        </div>

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Trail floor</span><span class="ctrl-val" id="v_trail_stop">99.5%</span></div>
          <input type="range" id="s_trail_stop" min="80" max="99.5" step="0.1" value="99.5">
          <div class="ctrl-desc">When the trail steps up, the new floor is set to this % of the current price — protecting that fraction of peak gains.</div>
        </div>

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Stop cooldown</span><span class="ctrl-val" id="v_stop_cooldown_days">4d</span></div>
          <input type="range" id="s_stop_cooldown_days" min="0" max="20" step="1" value="4">
          <div class="ctrl-desc">After a stop-sell, block additional stop-sells for this asset for N trading days. Trails can still move during the cooldown.</div>
        </div>

        <button class="run-btn" id="runBtn">Run Simulation</button>
      </div>
    </div>
  </aside>

  <!-- ── Main workspace ──────────────────────────────────────────────────── -->
  <main class="workspace">

    <div class="stat-band">
      <div class="stat">
        <div class="stat-label">Final value</div>
        <div class="stat-value" id="s_final">—</div>
        <div class="stat-sub" id="s_return"></div>
      </div>
      <div class="stat">
        <div class="stat-label">Max drawdown</div>
        <div class="stat-value" id="s_dd" style="color:var(--bad)">—</div>
      </div>
      <div class="stat">
        <div class="stat-label">Buy &amp; hold</div>
        <div class="stat-value" id="s_bh">—</div>
        <div class="stat-sub" id="s_bh_return"></div>
      </div>
      <div class="stat">
        <div class="stat-label">Events</div>
        <div class="stat-value" id="s_nevents">—</div>
        <div class="stat-sub" id="s_events_sub"></div>
      </div>
    </div>

    <!-- Beta band (shown after first run) -->
    <div class="beta-band" id="betaBand" style="display:none">
      <span class="beta-band-label">60-day β vs SPY</span>
      <div id="betaChips"></div>
    </div>

    <div class="chart-panel">
      <h3>Asset Prices — Indexed (day 0 = 100)</h3>
      <div class="chart-sub" id="priceSub">Run the simulation to load data.</div>
      <div class="chart-wrap"><canvas id="chartPrice"></canvas></div>
    </div>

    <div class="chart-panel">
      <h3>Portfolio Value — Algo (stacked) vs Buy &amp; Hold</h3>
      <div id="assetToggles" class="asset-toggles"></div>
      <div class="chart-sub" id="totalSub">&nbsp;</div>
      <div class="chart-wrap"><canvas id="chartTotal"></canvas></div>
    </div>

    <div class="events-panel">
      <h3>Decision Log</h3>
      <div id="filterChips" class="filter-chips"></div>
      <div class="tbl-wrap">
        <table>
          <thead>
            <tr><th>Date</th><th>Asset</th><th>Action</th><th>Price</th><th>Amount</th><th>Why</th></tr>
          </thead>
          <tbody id="eventsBody">
            <tr><td colspan="6" class="empty">Run a simulation to see decisions.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

  </main>
</div>

<script>
  let chartPrice = null;
  let chartTotal = null;
  let simState = null;
  let eventFilter = "ALL";
  let allSymbols = [];
  let selectedSymbols = [];
  let isRunning = false;

  // ── Sliders ──────────────────────────────────────────────────────────────────
  const sliders = [
    {{ id:"s_initial",       vid:"v_initial",       fmt: v => "$" + Number(v).toLocaleString() }},
    {{ id:"s_base_tol",      vid:"v_base_tol",      fmt: v => v + "%" }},
    {{ id:"s_stop_sell_pct", vid:"v_stop_sell_pct", fmt: v => v + "%" }},
    {{ id:"s_trail_step",    vid:"v_trail_step",    fmt: v => "+" + (v - 100) + "%" }},
    {{ id:"s_trail_stop",    vid:"v_trail_stop",    fmt: v => v + "%" }},
    {{ id:"s_stop_cooldown_days", vid:"v_stop_cooldown_days", fmt: v => v + "d" }},
  ];
  sliders.forEach(({{id, vid, fmt}}) => {{
    const el  = document.getElementById(id);
    const lbl = document.getElementById(vid);
    el.addEventListener("input", () => lbl.textContent = fmt(el.value));
    lbl.textContent = fmt(el.value);
  }});

  function getConfig() {{
    return {{
      initial:       parseFloat(document.getElementById("s_initial").value),
      base_tol:      parseFloat(document.getElementById("s_base_tol").value) / 100,
      stop_sell_pct: parseFloat(document.getElementById("s_stop_sell_pct").value) / 100,
      trail_step:    parseFloat(document.getElementById("s_trail_step").value) / 100,
      trail_stop:    parseFloat(document.getElementById("s_trail_stop").value) / 100,
      stop_cooldown_days: parseFloat(document.getElementById("s_stop_cooldown_days").value),
      symbols:       selectedSymbols.length ? selectedSymbols : allSymbols,
    }};
  }}

  function money(v, dec=0) {{
    if (v == null) return "—";
    return new Intl.NumberFormat(undefined, {{style:"currency",currency:"USD",maximumFractionDigits:dec}}).format(v);
  }}
  function signed(v) {{ return (v >= 0 ? "+" : "") + v.toFixed(1) + "%"; }}
  function badgeClass(action) {{
    if (action.startsWith("BUY"))     return "buy";
    if (action.startsWith("STOP"))    return "stop";
    if (action.startsWith("TRAIL"))   return "trail";
    if (action.startsWith("REALLOC")) return "realloc";
    return "buy";
  }}
  function eventPriority(action) {{
    if (action.startsWith("STOP")) return 0;
    if (action.startsWith("TRAIL")) return 1;
    if (action.startsWith("REALLOC")) return 2;
    if (action.startsWith("BUY")) return 3;
    return 4;
  }}
  const timeScale = {{
    type:"time", time:{{unit:"month", displayFormats:{{month:"MMM yy"}}}},
    ticks:{{color:"#93a09f", maxRotation:0}}, grid:{{color:"rgba(255,255,255,.04)"}}
  }};
  const tooltipBase = {{
    backgroundColor:"#111417", borderColor:"rgba(255,255,255,.08)",
    borderWidth:1, titleColor:"#dfe8df", bodyColor:"#dfe8df"
  }};

  async function toggleAssetSymbol(sym) {{
    const next = selectedSymbols.includes(sym)
      ? selectedSymbols.filter(s => s !== sym)
      : [...selectedSymbols, sym].sort((a, b) => allSymbols.indexOf(a) - allSymbols.indexOf(b));
    if (!next.length || isRunning) return;
    selectedSymbols = next;
    eventFilter = selectedSymbols.includes(eventFilter) ? eventFilter : "ALL";
    renderAssetToggles();
    await runSimulation();
  }}

  function renderPriceChart(dates, symbols, colors, prices, avg_betas, base_tol) {{
    if (chartPrice) chartPrice.destroy();
    const ctx = document.getElementById("chartPrice").getContext("2d");
    chartPrice = new Chart(ctx, {{
      type:"line",
      data:{{
        labels: dates,
        datasets: symbols.map((sym, idx) => ({{
          label: sym,
          data:  prices[sym],
          borderColor: colors[idx],
          borderWidth: 2,
          pointRadius: 0,
          tension: .15,
          fill: false,
        }})),
      }},
      options:{{
        responsive:true, maintainAspectRatio:false, animation:{{duration:280}},
        interaction:{{mode:"index", intersect:false}},
        plugins:{{
          legend:{{
            labels:{{color:"#93a09f", font:{{size:12}}}},
            onClick: async (_, item) => {{
              await toggleAssetSymbol(item.text);
            }},
          }},
          tooltip:{{...tooltipBase, callbacks:{{
            label: ctx => ctx.dataset.label + ": " + ctx.parsed.y.toFixed(1)
          }}}}
        }},
        scales:{{
          x: timeScale,
          y:{{ticks:{{color:"#93a09f"}}, grid:{{color:"rgba(255,255,255,.05)"}}}}
        }}
      }}
    }});
    // Update subtitle with effective floors
    const parts = symbols.map(sym => {{
      const b = avg_betas[sym] || 1;
      const floor = (base_tol * b * 100).toFixed(1);
      return sym + " β" + b.toFixed(1) + " → " + floor + "% floor";
    }});
    document.getElementById("priceSub").textContent = parts.join("  ·  ");
  }}

  function renderTotalChart(dates, history, stackSymbols, stackColors, bh) {{
    if (chartTotal) chartTotal.destroy();
    const ctx = document.getElementById("chartTotal").getContext("2d");
    chartTotal = new Chart(ctx, {{
      type:"line",
      data:{{
        labels: dates,
        datasets: [
          ...stackSymbols.map((sym, idx) => ({{
            label: sym,
            data:  history.map(h => h.assets[sym] ?? 0),
            borderColor: stackColors[idx],
            backgroundColor: stackColors[idx] + "50",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: .15,
            fill: true,
            stack: "portfolio",
          }})),
          {{
            label: "Buy & Hold",
            data:  bh,
            borderColor: "rgba(147,160,159,.6)",
            borderWidth: 2,
            borderDash: [6,4],
            pointRadius: 0,
            tension: .15,
            fill: false,
          }},
        ]
      }},
      options:{{
        responsive:true, maintainAspectRatio:false, animation:{{duration:280}},
        interaction:{{mode:"index", intersect:false}},
        plugins:{{
          legend:{{
            labels:{{color:"#93a09f", font:{{size:12}}}},
            onClick: async (_, item) => {{
              if (item.text === "Buy & Hold") return;
              if (item.text === "Cash") return;
              await toggleAssetSymbol(item.text);
            }},
          }},
          tooltip:{{...tooltipBase, callbacks:{{
            label: ctx => ctx.dataset.label + ": " + money(ctx.parsed.y, 2)
          }}}}
        }},
        scales:{{
          x: timeScale,
          y:{{stacked:true, ticks:{{color:"#93a09f", callback: v => money(v)}}, grid:{{color:"rgba(255,255,255,.05)"}}}}
        }}
      }}
    }});
  }}

  function renderBetaBand(symbols, colors, avg_betas, base_tol) {{
    const band  = document.getElementById("betaBand");
    const chips = document.getElementById("betaChips");
    band.style.display = "flex";
    chips.innerHTML = symbols.map((sym, idx) => {{
      const b     = avg_betas[sym] || 1;
      const floor = (base_tol * b * 100).toFixed(1);
      return `<span class="beta-chip">
        <span class="sym" style="color:${{colors[idx]}}">${{sym}}</span>
        <span class="bval">β ${{b.toFixed(2)}}</span>
        <span class="floor-hint">→ ${{floor}}% floor</span>
      </span>`;
    }}).join("");
  }}

  function renderEventFilters(symbols) {{
    const wrap = document.getElementById("filterChips");
    const items = ["ALL", ...symbols];
    wrap.innerHTML = items.map(sym =>
      `<button type="button" class="fchip${{eventFilter === sym ? " active" : ""}}" data-symbol="${{sym}}">${{sym === "ALL" ? "All assets" : sym}}</button>`
    ).join("");
    wrap.querySelectorAll(".fchip").forEach(btn => {{
      btn.addEventListener("click", () => {{
        eventFilter = btn.dataset.symbol;
        renderEventFilters(symbols);
        renderEvents(simState ? simState.events : []);
      }});
    }});
  }}

  function renderEvents(events) {{
    const tbody = document.getElementById("eventsBody");
    const filtered = (eventFilter === "ALL" ? events : events.filter(e => e.symbol === eventFilter))
      .slice()
      .sort((a, b) => {{
        const byDate = a.date.localeCompare(b.date);
        if (byDate !== 0) return byDate;
        return eventPriority(a.action) - eventPriority(b.action);
      }});
    if (!filtered.length) {{
      tbody.innerHTML = `<tr><td colspan="6" class="empty">No events.</td></tr>`;
      return;
    }}
    tbody.innerHTML = filtered.map(e => `
      <tr>
        <td class="mono" style="white-space:nowrap">${{e.date}}</td>
        <td><span class="sym-chip">${{e.symbol}}</span></td>
        <td><span class="badge ${{badgeClass(e.action)}}">${{e.action}}</span></td>
        <td class="mono">${{e.price  != null ? money(e.price,  2) : "—"}}</td>
        <td class="mono">${{e.amount != null ? money(e.amount, 2) : "—"}}</td>
        <td class="reason">${{e.reason}}</td>
      </tr>`).join("");
  }}

  function renderSummary(s) {{
    const col = v => v >= 0 ? "var(--good)" : "var(--bad)";
    const fin = document.getElementById("s_final");
    fin.textContent  = money(s.final);
    fin.style.color  = col(s.return_pct);
    document.getElementById("s_return").textContent    = signed(s.return_pct);
    document.getElementById("s_dd").textContent        = "-" + s.max_dd_pct.toFixed(1) + "%";
    const bh = document.getElementById("s_bh");
    bh.textContent   = money(s.bh_final);
    bh.style.color   = col(s.bh_return_pct);
    document.getElementById("s_bh_return").textContent = signed(s.bh_return_pct);
    document.getElementById("s_nevents").textContent   = s.n_stops + s.n_trails;
    document.getElementById("s_events_sub").textContent =
      s.n_stops + " stops · " + s.n_trails + " trails";
  }}
  function renderAssetToggles() {{
    const wrap = document.getElementById("assetToggles");
    if (!allSymbols.length) {{
      wrap.innerHTML = "";
      return;
    }}
    wrap.innerHTML = allSymbols.map((sym, idx) => {{
      const active = selectedSymbols.includes(sym);
      const bg = simState && simState.symbols.includes(sym) ? simState.colors[simState.symbols.indexOf(sym)] : "#2c3438";
      return `<button type="button" class="asset-toggle${{active ? " active" : " off"}}" data-symbol="${{sym}}" style="background:${{active ? bg : "transparent"}}">${{sym}}</button>`;
    }}).join("");
    wrap.querySelectorAll(".asset-toggle").forEach(btn => {{
      btn.addEventListener("click", async () => {{
        await toggleAssetSymbol(btn.dataset.symbol);
      }});
    }});
  }}

  async function runSimulation() {{
    const btn = document.getElementById("runBtn");
    btn.disabled = true;
    btn.textContent = "Running…";
    isRunning = true;
    try {{
      const cfg  = getConfig();
      const res  = await fetch("/api/simulate", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify(cfg),
      }});
      const data = await res.json();
      if (!data.ok) throw new Error(data.message);
      const r = data.result;
      allSymbols = r.all_symbols;
      selectedSymbols = r.symbols;
      simState = r;
      renderPriceChart(r.dates, r.symbols, r.colors, r.prices, r.avg_betas, cfg.base_tol);
      renderTotalChart(r.dates, r.history, r.stack_symbols, r.stack_colors, r.bh);
      renderBetaBand(r.symbols, r.colors, r.avg_betas, cfg.base_tol);
      renderAssetToggles();
      renderEventFilters(r.symbols);
      renderEvents(r.events);
      renderSummary(r.summary);
      const hiddenCount = allSymbols.length - r.symbols.length;
      const hiddenText = hiddenCount
        ? ` · rerun without ${{hiddenCount}} excluded asset${{hiddenCount === 1 ? "" : "s"}}`
        : "";
      document.getElementById("totalSub").textContent =
        `Simulated portfolio: ${{r.symbols.join(" · ")}}${{hiddenText}}`;
    }} catch(err) {{
      alert("Error: " + err.message);
    }} finally {{
      isRunning = false;
      btn.disabled = false;
      btn.textContent = "Run Simulation";
    }}
  }}

  // ── Run ───────────────────────────────────────────────────────────────────────
  document.getElementById("runBtn").addEventListener("click", async () => {{
    eventFilter = "ALL";
    await runSimulation();
  }});

  // ── Data status ───────────────────────────────────────────────────────────────
  fetch("/api/data-status").then(r=>r.json()).then(d=>{{
    allSymbols = d.symbols || [];
    if (!selectedSymbols.length) selectedSymbols = [...allSymbols];
    renderAssetToggles();
    document.getElementById("dataStatus").textContent = d.ready
      ? d.symbols.join(" · ") + " · " + d.days + " days · " + d.start + " → " + d.end
      : "Ready — click Run";
  }}).catch(()=>{{
    document.getElementById("dataStatus").textContent = "Ready — click Run";
  }});
</script>
</body>
</html>"""


# ── HTTP server ───────────────────────────────────────────────────────────────

def json_resp(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200):
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = build_html().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/data-status":
            try:
                d = load_data()
                json_resp(self, {
                    "ok": True, "ready": True,
                    "days":    len(d["dates"]),
                    "start":   d["dates"][0],
                    "end":     d["dates"][-1],
                    "symbols": [display(s) for s in SYMBOLS],
                })
            except Exception:
                json_resp(self, {"ok": True, "ready": False})
        else:
            json_resp(self, {"ok": False, "message": "Not found"}, 404)

    def do_POST(self):
        if urlparse(self.path).path == "/api/simulate":
            try:
                length  = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                cfg     = SimConfig.from_dict(payload)
                symbols = normalize_symbols(payload.get("symbols"))
                data    = load_data()
                result  = simulate(cfg, data, symbols)
                json_resp(self, {"ok": True, "result": result})
            except Exception as exc:
                json_resp(self, {"ok": False, "message": str(exc)}, 400)
        else:
            json_resp(self, {"ok": False, "message": "Not found"}, 404)


def main():
    port = PORT
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Portfolio sandbox → http://localhost:{port}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.4, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    threading.Thread(target=load_data, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
