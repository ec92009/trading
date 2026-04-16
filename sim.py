"""
Trading algo sandbox — 1-year historical back-test.

Simulates the stop-loss / trailing / ladder / redistribution algo over a year
of daily OHLCV data for five assets, starting with an even portfolio split.

Usage:
    python3 sim.py
    python3 sim.py --no-browser --port 8093
"""
from __future__ import annotations

import json
import sys
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yfinance as yf

PORT = 8093
HERE = Path(__file__).parent
VERSION_PATH = HERE / "VERSION"

SYMBOLS = ["TSLA", "TSM", "NVDA", "PLTR", "BTC"]
YF_MAP  = {"TSLA": "TSLA", "TSM": "TSM", "NVDA": "NVDA", "PLTR": "PLTR", "BTC": "BTC-USD"}
COLORS  = {"TSLA": "#d8b27a", "TSM": "#73b7ff", "NVDA": "#71d6ad", "PLTR": "#ff8f70", "BTC": "#c4a7ff"}

_cache: dict | None = None
_cache_lock = threading.Lock()


def read_version() -> str:
    if VERSION_PATH.exists():
        v = VERSION_PATH.read_text().strip()
        return v if v.startswith("v") else f"v{v}"
    return "v0.0"


# ── Data ──────────────────────────────────────────────────────────────────────

def load_data() -> dict:
    """Download (or return cached) 1 year of daily OHLCV. Returns {sym: df}."""
    global _cache
    with _cache_lock:
        if _cache is not None:
            return _cache
        result: dict = {}
        for sym, yf_sym in YF_MAP.items():
            df = yf.Ticker(yf_sym).history(period="1y")[["Close", "Low", "High"]].dropna()
            df.index = [d.date() for d in df.index]
            result[sym] = df
        common = sorted(set.intersection(*[set(df.index) for df in result.values()]))
        for sym in result:
            result[sym] = result[sym].loc[common]
        _cache = result
        return result


# ── Simulation ────────────────────────────────────────────────────────────────

@dataclass
class SimConfig:
    initial:       float = 1000.0
    stop_pct:      float = 0.95
    trail_trigger: float = 1.10
    trail_step:    float = 1.05
    trail_stop:    float = 0.95
    stop_sell_pct: float = 0.50
    ladder1_pct:   float = 0.925
    ladder2_pct:   float = 0.850

    @classmethod
    def from_dict(cls, d: dict) -> "SimConfig":
        valid = set(cls.__dataclass_fields__)
        return cls(**{k: float(v) for k, v in d.items() if k in valid})


def simulate(cfg: SimConfig, data: dict) -> dict:
    """Run the algo over historical data. Returns history, events, summary."""
    dates = list(data[SYMBOLS[0]].index)
    n     = len(SYMBOLS)
    per   = cfg.initial / n

    class A:
        """Per-asset mutable state."""
        __slots__ = ("sym", "qty", "floor", "trail_next", "l1", "l2",
                     "l1_done", "l2_done", "half_stopped", "stopped")
        def __init__(self, sym: str, price: float):
            self.sym          = sym
            self.qty          = per / price
            self.floor        = price * cfg.stop_pct
            self.trail_next   = price * cfg.trail_trigger
            self.l1           = self.floor * cfg.ladder1_pct
            self.l2           = self.floor * cfg.ladder2_pct
            self.l1_done      = False
            self.l2_done      = False
            self.half_stopped = False
            self.stopped      = False
        def value(self, price: float) -> float:
            return self.qty * price

    assets = {sym: A(sym, float(data[sym]["Close"].iloc[0])) for sym in SYMBOLS}
    cash   = 0.0
    history: list[dict] = []
    events:  list[dict] = []

    def snap(d):
        total = cash
        row: dict = {"date": str(d)}
        for sym, a in assets.items():
            p = float(data[sym].loc[d, "Close"])
            v = round(a.value(p), 2)
            row[sym] = v
            total += v
        row["total"] = round(total + cash, 2) if False else round(total, 2)
        history.append(row)

    def reallocate(amount: float, exclude: set, d):
        nonlocal cash
        active = {s: a for s, a in assets.items() if not a.stopped and s not in exclude}
        if not active:
            cash += amount
            return
        prices    = {s: float(data[s].loc[d, "Close"]) for s in active}
        total_val = sum(a.value(prices[s]) for s, a in active.items())
        for s, a in active.items():
            w     = (a.value(prices[s]) / total_val) if total_val > 0 else (1 / len(active))
            share = round(amount * w, 2)
            if share < 0.01:
                continue
            a.qty += share / prices[s]
            events.append({"date": str(d), "sym": s, "action": "REALLOC",
                           "price": round(prices[s], 2), "amount": share})

    snap(dates[0])

    for d in dates[1:]:
        stop_cash:    float = 0.0
        stop_sources: set   = set()

        for sym, a in assets.items():
            if a.stopped:
                continue
            close = float(data[sym].loc[d, "Close"])
            low   = float(data[sym].loc[d, "Low"])

            # ── Stop ──────────────────────────────────────────────────────────
            if low <= a.floor:
                sp = a.floor
                if not a.half_stopped:
                    sell_qty  = a.qty * cfg.stop_sell_pct
                    proceeds  = sell_qty * sp
                    a.qty    -= sell_qty
                    a.half_stopped = True
                    a.floor      = sp * cfg.stop_pct
                    a.trail_next = sp * cfg.trail_trigger
                    stop_cash   += proceeds
                    stop_sources.add(sym)
                    events.append({"date": str(d), "sym": sym,
                                   "action": f"STOP {int(cfg.stop_sell_pct*100)}%",
                                   "price": round(sp, 2), "amount": round(proceeds, 2)})
                else:
                    proceeds    = a.qty * sp
                    stop_cash  += proceeds
                    stop_sources.add(sym)
                    a.qty       = 0.0
                    a.stopped   = True
                    events.append({"date": str(d), "sym": sym, "action": "STOP FINAL",
                                   "price": round(sp, 2), "amount": round(proceeds, 2)})
                continue  # skip trailing/ladders on stop day

            # ── Trail ─────────────────────────────────────────────────────────
            if close >= a.trail_next:
                new_floor = close * cfg.trail_stop
                if new_floor > a.floor:
                    a.floor      = new_floor
                    a.trail_next = close * cfg.trail_step
                    events.append({"date": str(d), "sym": sym, "action": "TRAIL",
                                   "price": round(close, 2), "amount": None})

            # ── Ladders (funded from available cash pool) ─────────────────────
            if not a.l1_done and low <= a.l1 and cash >= 1.0:
                notional  = min(cash, per * 0.5)
                cash     -= notional
                a.qty    += notional / a.l1
                a.l1_done = True
                events.append({"date": str(d), "sym": sym, "action": "LADDER 1",
                               "price": round(a.l1, 2), "amount": round(notional, 2)})
            if not a.l2_done and low <= a.l2 and cash >= 1.0:
                notional  = min(cash, per * 0.5)
                cash     -= notional
                a.qty    += notional / a.l2
                a.l2_done = True
                events.append({"date": str(d), "sym": sym, "action": "LADDER 2",
                               "price": round(a.l2, 2), "amount": round(notional, 2)})

        if stop_cash > 0:
            reallocate(stop_cash, stop_sources, d)
        snap(d)

    # ── Buy-and-hold baseline ─────────────────────────────────────────────────
    init_qty = {sym: per / float(data[sym]["Close"].iloc[0]) for sym in SYMBOLS}
    bh = []
    for row in history:
        d_obj = __import__("datetime").date.fromisoformat(row["date"])
        bh.append(round(sum(init_qty[s] * float(data[s].loc[d_obj, "Close"]) for s in SYMBOLS), 2))

    # ── Summary stats ─────────────────────────────────────────────────────────
    totals = [r["total"] for r in history]
    peak   = totals[0]; max_dd = 0.0
    for t in totals:
        peak   = max(peak, t)
        max_dd = max(max_dd, (peak - t) / peak)

    final    = totals[-1]
    bh_final = bh[-1]
    return {
        "dates":   [r["date"] for r in history],
        "history": history,
        "bh":      bh,
        "events":  events,
        "summary": {
            "initial":       cfg.initial,
            "final":         round(final, 2),
            "return_pct":    round((final - cfg.initial) / cfg.initial * 100, 2),
            "max_dd_pct":    round(max_dd * 100, 2),
            "bh_final":      round(bh_final, 2),
            "bh_return_pct": round((bh_final - cfg.initial) / cfg.initial * 100, 2),
            "n_stops":    sum(1 for e in events if "STOP"   in e["action"]),
            "n_trails":   sum(1 for e in events if e["action"] == "TRAIL"),
            "n_ladders":  sum(1 for e in events if "LADDER" in e["action"]),
            "n_reallocs": sum(1 for e in events if e["action"] == "REALLOC"),
        },
    }


# ── HTML ──────────────────────────────────────────────────────────────────────

def build_html() -> str:
    ver     = read_version()
    sym_js  = json.dumps(SYMBOLS)
    col_js  = json.dumps(COLORS)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Algo Sandbox</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
  <style>
    :root {{
      --bg:#111417; --panel:#181d20; --line:rgba(216,178,122,.18);
      --accent:#d8b27a; --text:#edf0eb; --muted:#93a09f;
      --good:#71d6ad; --bad:#ff8f70; --shadow:0 30px 60px rgba(0,0,0,.28);
    }}
    * {{ box-sizing:border-box; }}
    html,body {{ margin:0; min-height:100%; background:radial-gradient(circle at top left,rgba(216,178,122,.08),transparent 32%),linear-gradient(180deg,#0d1012 0%,#12171a 100%); color:var(--text); font-family:"Space Grotesk",sans-serif; overflow-x:hidden; }}
    body {{ padding:24px; }}
    button,input,select {{ font:inherit; }}
    .shell {{ width:min(100%,1520px); margin:0 auto; display:grid; grid-template-columns:300px minmax(0,1fr); gap:20px; }}
    .topbar {{ grid-column:1/-1; display:flex; justify-content:space-between; align-items:center; padding-bottom:12px; border-bottom:1px solid var(--line); }}
    .brand {{ color:var(--accent); text-transform:uppercase; letter-spacing:.18em; font-size:13px; font-weight:700; }}
    .pill {{ display:inline-flex; align-items:center; gap:6px; border:1px solid var(--line); padding:8px 14px; border-radius:999px; color:var(--muted); font-size:13px; background:rgba(255,255,255,.02); }}
    .panel {{ background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,.01)); border:1px solid rgba(255,255,255,.06); border-radius:24px; padding:20px; box-shadow:var(--shadow); }}
    .panel h2 {{ margin:0 0 16px; font-size:15px; }}
    .ctrl {{ display:grid; gap:14px; }}
    .ctrl-group {{ display:grid; gap:6px; }}
    .ctrl-label {{ display:flex; justify-content:space-between; align-items:baseline; font-size:12px; text-transform:uppercase; letter-spacing:.1em; color:var(--muted); }}
    .ctrl-val {{ font-family:"IBM Plex Mono",monospace; font-size:13px; color:var(--text); }}
    input[type=range] {{ width:100%; accent-color:var(--accent); cursor:pointer; }}
    .divider {{ border:0; border-top:1px solid rgba(255,255,255,.06); margin:16px 0; }}
    .ctrl-row {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
    .field {{ display:grid; gap:6px; }}
    .field label {{ font-size:12px; text-transform:uppercase; letter-spacing:.1em; color:var(--muted); }}
    input[type=number] {{ width:100%; border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.03); color:var(--text); border-radius:12px; padding:10px 12px; outline:none; transition:.2s; }}
    input[type=number]:focus {{ border-color:rgba(216,178,122,.55); background:rgba(255,255,255,.05); }}
    .run-btn {{ width:100%; border:0; border-radius:999px; padding:14px; cursor:pointer; background:var(--accent); color:#171311; font-weight:700; font-size:15px; transition:.18s; margin-top:6px; }}
    .run-btn:hover {{ transform:translateY(-1px); opacity:.92; }}
    .run-btn:disabled {{ opacity:.45; cursor:not-allowed; transform:none; }}
    .workspace {{ display:grid; gap:18px; }}
    .stat-band {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; }}
    .stat {{ padding:16px 18px; border-radius:20px; background:linear-gradient(90deg,rgba(255,255,255,.03),rgba(255,255,255,.01)); border:1px solid rgba(255,255,255,.06); }}
    .stat-label {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.12em; margin-bottom:10px; }}
    .stat-value {{ font-size:28px; line-height:1; letter-spacing:-.03em; font-weight:700; }}
    .stat-sub {{ font-size:12px; color:var(--muted); margin-top:4px; }}
    .chart-panel {{ padding:20px; border-radius:24px; background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,.01)); border:1px solid rgba(255,255,255,.06); }}
    .chart-panel h3 {{ margin:0 0 16px; font-size:14px; color:var(--muted); text-transform:uppercase; letter-spacing:.1em; }}
    .chart-wrap {{ position:relative; height:300px; }}
    .events-panel {{ padding:20px; border-radius:24px; background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,.01)); border:1px solid rgba(255,255,255,.06); }}
    .events-panel h3 {{ margin:0 0 12px; font-size:14px; color:var(--muted); text-transform:uppercase; letter-spacing:.1em; }}
    .filter-row {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }}
    .filter-btn {{ border:1px solid rgba(255,255,255,.1); background:rgba(255,255,255,.04); color:var(--muted); border-radius:999px; padding:6px 14px; cursor:pointer; font-size:12px; transition:.15s; }}
    .filter-btn.active {{ background:rgba(216,178,122,.18); border-color:rgba(216,178,122,.4); color:var(--accent); }}
    .tbl-wrap {{ max-height:320px; overflow-y:auto; border-radius:14px; border:1px solid rgba(255,255,255,.05); }}
    table {{ width:100%; border-collapse:collapse; font-size:12px; }}
    th,td {{ padding:9px 12px; text-align:left; border-bottom:1px solid rgba(255,255,255,.04); }}
    th {{ color:var(--muted); text-transform:uppercase; letter-spacing:.1em; font-size:11px; position:sticky; top:0; background:#14191c; }}
    td {{ color:var(--text); font-family:"IBM Plex Mono",monospace; }}
    tr:last-child td {{ border-bottom:0; }}
    .badge {{ display:inline-flex; padding:3px 8px; border-radius:999px; font-size:11px; border:1px solid rgba(255,255,255,.08); }}
    .badge.stop    {{ color:var(--bad);  border-color:rgba(255,143,112,.3); background:rgba(255,143,112,.08); }}
    .badge.trail   {{ color:var(--good); border-color:rgba(113,214,173,.3); background:rgba(113,214,173,.08); }}
    .badge.ladder  {{ color:#c4a7ff;     border-color:rgba(196,167,255,.3); background:rgba(196,167,255,.08); }}
    .badge.realloc {{ color:#73b7ff;     border-color:rgba(115,183,255,.3); background:rgba(115,183,255,.08); }}
    .empty {{ color:var(--muted); padding:40px 0; text-align:center; }}
    .loading {{ color:var(--accent); padding:40px 0; text-align:center; animation:pulse 1.4s ease infinite; }}
    @keyframes pulse {{ 0%,100%{{opacity:.5}} 50%{{opacity:1}} }}
    @media(max-width:900px) {{ .shell{{grid-template-columns:1fr}} .stat-band{{grid-template-columns:repeat(2,1fr)}} }}
  </style>
</head>
<body>
<div class="shell">
  <div class="topbar">
    <div class="brand">Algo Sandbox</div>
    <div style="display:flex;gap:10px;align-items:center">
      <div class="pill" id="dataStatus">Loading market data…</div>
      <div class="pill">{ver}</div>
    </div>
  </div>

  <!-- Sidebar controls -->
  <aside>
    <div class="panel">
      <h2>Simulation Controls</h2>
      <div class="ctrl">

        <div class="ctrl-group">
          <div class="ctrl-label">Initial portfolio <span class="ctrl-val" id="v_initial">$1,000</span></div>
          <input type="range" id="s_initial" min="500" max="10000" step="500" value="1000">
        </div>
        <hr class="divider">
        <div class="ctrl-group">
          <div class="ctrl-label">Stop floor <span class="ctrl-val" id="v_stop_pct">95%</span></div>
          <input type="range" id="s_stop_pct" min="80" max="99" step="1" value="95">
          <div style="font-size:11px;color:var(--muted)">Floor = entry × this%. Hit → sell portion.</div>
        </div>
        <div class="ctrl-group">
          <div class="ctrl-label">Stop sell % <span class="ctrl-val" id="v_stop_sell_pct">50%</span></div>
          <input type="range" id="s_stop_sell_pct" min="10" max="100" step="5" value="50">
          <div style="font-size:11px;color:var(--muted)">How much to sell on first stop hit.</div>
        </div>
        <hr class="divider">
        <div class="ctrl-group">
          <div class="ctrl-label">Trail trigger <span class="ctrl-val" id="v_trail_trigger">+10%</span></div>
          <input type="range" id="s_trail_trigger" min="101" max="150" step="1" value="110">
        </div>
        <div class="ctrl-group">
          <div class="ctrl-label">Trail step <span class="ctrl-val" id="v_trail_step">+5%</span></div>
          <input type="range" id="s_trail_step" min="101" max="130" step="1" value="105">
        </div>
        <div class="ctrl-group">
          <div class="ctrl-label">Trail floor <span class="ctrl-val" id="v_trail_stop">95%</span></div>
          <input type="range" id="s_trail_stop" min="80" max="99" step="1" value="95">
        </div>
        <hr class="divider">
        <div class="ctrl-group">
          <div class="ctrl-label">Ladder 1 level <span class="ctrl-val" id="v_ladder1_pct">92.5%</span></div>
          <input type="range" id="s_ladder1_pct" min="70" max="99" step="0.5" value="92.5">
          <div style="font-size:11px;color:var(--muted)">Of floor. Buy if price drops here.</div>
        </div>
        <div class="ctrl-group">
          <div class="ctrl-label">Ladder 2 level <span class="ctrl-val" id="v_ladder2_pct">85%</span></div>
          <input type="range" id="s_ladder2_pct" min="50" max="98" step="0.5" value="85">
        </div>

        <button class="run-btn" id="runBtn">Run Simulation</button>
      </div>
    </div>
  </aside>

  <!-- Main workspace -->
  <main class="workspace">
    <div class="stat-band" id="statBand">
      <div class="stat"><div class="stat-label">Final Value</div><div class="stat-value" id="s_final">—</div><div class="stat-sub" id="s_return"></div></div>
      <div class="stat"><div class="stat-label">Max Drawdown</div><div class="stat-value" id="s_dd">—</div></div>
      <div class="stat"><div class="stat-label">Buy &amp; Hold</div><div class="stat-value" id="s_bh">—</div><div class="stat-sub" id="s_bh_return"></div></div>
      <div class="stat"><div class="stat-label">Events</div><div class="stat-value" id="s_events">—</div><div class="stat-sub" id="s_events_sub"></div></div>
    </div>

    <div class="chart-panel">
      <h3>Portfolio Value — Algo vs Buy &amp; Hold</h3>
      <div class="chart-wrap"><canvas id="chartTotal"></canvas></div>
    </div>

    <div class="chart-panel">
      <h3>Per-Asset Value Over Time</h3>
      <div class="chart-wrap"><canvas id="chartAssets"></canvas></div>
    </div>

    <div class="events-panel">
      <h3>Event Log</h3>
      <div class="filter-row" id="filterRow">
        <button class="filter-btn active" data-filter="ALL">All</button>
        <button class="filter-btn" data-filter="STOP">Stops</button>
        <button class="filter-btn" data-filter="TRAIL">Trails</button>
        <button class="filter-btn" data-filter="LADDER">Ladders</button>
        <button class="filter-btn" data-filter="REALLOC">Reallocs</button>
      </div>
      <div class="tbl-wrap">
        <table>
          <thead><tr><th>Date</th><th>Symbol</th><th>Action</th><th>Price</th><th>Amount</th></tr></thead>
          <tbody id="eventsBody"><tr><td colspan="5" class="empty">Run a simulation to see events.</td></tr></tbody>
        </table>
      </div>
    </div>
  </main>
</div>

<script>
  const SYMBOLS = {sym_js};
  const COLORS  = {col_js};
  let chartTotal  = null;
  let chartAssets = null;
  let allEvents   = [];
  let activeFilter = "ALL";

  // ── Sliders ─────────────────────────────────────────────────────────────────
  const sliders = [
    {{ id:"s_initial",       label:"v_initial",       fmt: v => "$" + Number(v).toLocaleString() }},
    {{ id:"s_stop_pct",      label:"v_stop_pct",      fmt: v => v + "%" }},
    {{ id:"s_stop_sell_pct", label:"v_stop_sell_pct", fmt: v => v + "%" }},
    {{ id:"s_trail_trigger", label:"v_trail_trigger", fmt: v => "+" + (v - 100) + "%" }},
    {{ id:"s_trail_step",    label:"v_trail_step",    fmt: v => "+" + (v - 100) + "%" }},
    {{ id:"s_trail_stop",    label:"v_trail_stop",    fmt: v => v + "%" }},
    {{ id:"s_ladder1_pct",   label:"v_ladder1_pct",   fmt: v => v + "%" }},
    {{ id:"s_ladder2_pct",   label:"v_ladder2_pct",   fmt: v => v + "%" }},
  ];
  sliders.forEach(({{id, label, fmt}}) => {{
    const el = document.getElementById(id);
    const lbl = document.getElementById(label);
    const update = () => lbl.textContent = fmt(el.value);
    el.addEventListener("input", update);
    update();
  }});

  function getConfig() {{
    return {{
      initial:       parseFloat(document.getElementById("s_initial").value),
      stop_pct:      parseFloat(document.getElementById("s_stop_pct").value) / 100,
      stop_sell_pct: parseFloat(document.getElementById("s_stop_sell_pct").value) / 100,
      trail_trigger: parseFloat(document.getElementById("s_trail_trigger").value) / 100,
      trail_step:    parseFloat(document.getElementById("s_trail_step").value) / 100,
      trail_stop:    parseFloat(document.getElementById("s_trail_stop").value) / 100,
      ladder1_pct:   parseFloat(document.getElementById("s_ladder1_pct").value) / 100,
      ladder2_pct:   parseFloat(document.getElementById("s_ladder2_pct").value) / 100,
    }};
  }}

  // ── Helpers ──────────────────────────────────────────────────────────────────
  function money(v) {{
    if (v == null) return "—";
    return new Intl.NumberFormat(undefined, {{style:"currency", currency:"USD", maximumFractionDigits:0}}).format(v);
  }}
  function signedPct(v) {{
    return (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
  }}
  function badgeClass(action) {{
    if (action.includes("STOP"))   return "stop";
    if (action === "TRAIL")        return "trail";
    if (action.includes("LADDER")) return "ladder";
    if (action === "REALLOC")      return "realloc";
    return "";
  }}

  // ── Charts ────────────────────────────────────────────────────────────────────
  const chartDefaults = {{
    responsive: true, maintainAspectRatio: false,
    animation: {{ duration: 300 }},
    interaction: {{ mode:"index", intersect:false }},
    plugins: {{
      legend: {{ labels: {{ color:"#93a09f", font:{{size:12}} }} }},
      tooltip: {{ backgroundColor:"#111417", borderColor:"rgba(255,255,255,.08)", borderWidth:1, titleColor:"#dfe8df", bodyColor:"#dfe8df" }}
    }},
    scales: {{
      x: {{ type:"time", time:{{ unit:"month", displayFormats:{{month:"MMM yy"}} }}, ticks:{{color:"#93a09f", maxRotation:0}}, grid:{{color:"rgba(255,255,255,.04)"}} }},
      y: {{ ticks:{{color:"#93a09f", callback: v => "$" + v.toLocaleString()}}, grid:{{color:"rgba(255,255,255,.05)"}} }}
    }}
  }};

  function renderTotalChart(dates, totals, bh) {{
    if (chartTotal) chartTotal.destroy();
    const ctx = document.getElementById("chartTotal").getContext("2d");
    chartTotal = new Chart(ctx, {{
      type: "line",
      data: {{
        labels: dates,
        datasets: [
          {{ label:"Algo", data:totals, borderColor:"#d8b27a", borderWidth:2.5, pointRadius:0, tension:.2, fill:false }},
          {{ label:"Buy & Hold", data:bh, borderColor:"rgba(147,160,159,.5)", borderWidth:1.5, borderDash:[5,4], pointRadius:0, tension:.2, fill:false }},
        ]
      }},
      options: chartDefaults,
    }});
  }}

  function renderAssetsChart(dates, history) {{
    if (chartAssets) chartAssets.destroy();
    const ctx = document.getElementById("chartAssets").getContext("2d");
    const datasets = SYMBOLS.map(sym => ({{
      label: sym,
      data: history.map(r => r[sym] ?? 0),
      borderColor: COLORS[sym],
      borderWidth: 1.8,
      pointRadius: 0,
      tension: .2,
      fill: false,
    }}));
    chartAssets = new Chart(ctx, {{
      type:"line",
      data: {{ labels:dates, datasets }},
      options: chartDefaults,
    }});
  }}

  // ── Events table ──────────────────────────────────────────────────────────────
  function renderEvents(events, filter) {{
    const tbody = document.getElementById("eventsBody");
    const shown = filter === "ALL" ? events : events.filter(e => e.action.includes(filter));
    if (!shown.length) {{
      tbody.innerHTML = `<tr><td colspan="5" class="empty">No ${{filter}} events.</td></tr>`;
      return;
    }}
    tbody.innerHTML = [...shown].reverse().map(e => `
      <tr>
        <td>${{e.date}}</td>
        <td style="color:${{COLORS[e.sym] || "inherit"}}">${{e.sym}}</td>
        <td><span class="badge ${{badgeClass(e.action)}}">${{e.action}}</span></td>
        <td>${{e.price != null ? "$" + Number(e.price).toLocaleString(undefined,{{maximumFractionDigits:2}}) : "—"}}</td>
        <td>${{e.amount != null ? "$" + Number(e.amount).toFixed(2) : "—"}}</td>
      </tr>`).join("");
  }}

  document.getElementById("filterRow").addEventListener("click", e => {{
    const btn = e.target.closest(".filter-btn");
    if (!btn) return;
    activeFilter = btn.dataset.filter;
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.toggle("active", b === btn));
    renderEvents(allEvents, activeFilter);
  }});

  // ── Summary cards ─────────────────────────────────────────────────────────────
  function renderSummary(s) {{
    const good = v => v >= 0 ? "var(--good)" : "var(--bad)";
    document.getElementById("s_final").textContent  = money(s.final);
    document.getElementById("s_final").style.color  = good(s.return_pct);
    document.getElementById("s_return").textContent = signedPct(s.return_pct);
    document.getElementById("s_dd").textContent     = "-" + s.max_dd_pct.toFixed(1) + "%";
    document.getElementById("s_dd").style.color     = "var(--bad)";
    document.getElementById("s_bh").textContent     = money(s.bh_final);
    document.getElementById("s_bh").style.color     = good(s.bh_return_pct);
    document.getElementById("s_bh_return").textContent = signedPct(s.bh_return_pct);
    document.getElementById("s_events").textContent = s.n_stops + s.n_trails + s.n_ladders + s.n_reallocs;
    document.getElementById("s_events_sub").textContent =
      `${{s.n_stops}} stops · ${{s.n_trails}} trails · ${{s.n_ladders}} ladders`;
  }}

  // ── Run ───────────────────────────────────────────────────────────────────────
  document.getElementById("runBtn").addEventListener("click", async () => {{
    const btn = document.getElementById("runBtn");
    btn.disabled = true;
    btn.textContent = "Running…";
    try {{
      const res = await fetch("/api/simulate", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(getConfig()),
      }});
      const data = await res.json();
      if (!data.ok) throw new Error(data.message);
      const r = data.result;
      allEvents = r.events;
      renderTotalChart(r.dates, r.history.map(h => h.total), r.bh);
      renderAssetsChart(r.dates, r.history);
      renderEvents(allEvents, activeFilter);
      renderSummary(r.summary);
    }} catch (err) {{
      alert("Simulation error: " + err.message);
    }} finally {{
      btn.disabled = false;
      btn.textContent = "Run Simulation";
    }}
  }});

  // ── Data status ───────────────────────────────────────────────────────────────
  fetch("/api/data-status").then(r => r.json()).then(d => {{
    document.getElementById("dataStatus").textContent = d.ready
      ? `${{d.days}} trading days · ${{d.start}} → ${{d.end}}`
      : "Click Run to fetch data";
  }}).catch(() => {{
    document.getElementById("dataStatus").textContent = "Click Run to fetch data";
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
                data = load_data()
                dates = list(data[SYMBOLS[0]].index)
                json_resp(self, {"ok": True, "ready": True,
                                 "days": len(dates),
                                 "start": str(dates[0]),
                                 "end":   str(dates[-1])})
            except Exception:
                json_resp(self, {"ok": True, "ready": False})
        else:
            json_resp(self, {"ok": False, "message": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/simulate":
            try:
                length  = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                cfg     = SimConfig.from_dict(payload)
                data    = load_data()
                result  = simulate(cfg, data)
                json_resp(self, {"ok": True, "result": result})
            except Exception as exc:
                json_resp(self, {"ok": False, "message": str(exc)}, 400)
        else:
            json_resp(self, {"ok": False, "message": "Not found"}, 404)


def main():
    port = PORT
    if "--port" in sys.argv:
        idx  = sys.argv.index("--port")
        port = int(sys.argv[idx + 1])
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Algo sandbox → http://localhost:{port}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.4, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    # Pre-fetch data in background so first Run is fast
    threading.Thread(target=load_data, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
