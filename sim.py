"""
Trading algo sandbox — 1-year TSLA back-test with plain-English event log.

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

_cache: dict | None = None
_cache_lock = threading.Lock()


def read_version() -> str:
    if VERSION_PATH.exists():
        v = VERSION_PATH.read_text().strip()
        return v if v.startswith("v") else f"v{v}"
    return "v0.0"


# ── Data ──────────────────────────────────────────────────────────────────────

def load_data() -> dict:
    """Download (or return cached) 1 year of daily OHLCV for TSLA."""
    global _cache
    with _cache_lock:
        if _cache is not None:
            return _cache
        df = yf.Ticker("TSLA").history(period="1y")[["Close", "Low", "High"]].dropna()
        df.index = [d.date() for d in df.index]
        dates  = list(df.index)
        closes = [round(float(v), 2) for v in df["Close"]]
        lows   = [round(float(v), 2) for v in df["Low"]]
        highs  = [round(float(v), 2) for v in df["High"]]
        _cache = {"dates": dates, "closes": closes, "lows": lows, "highs": highs}
        return _cache


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
    dates  = data["dates"]
    closes = data["closes"]
    lows   = data["lows"]

    # ── Initial buy at day-0 close ────────────────────────────────────────────
    entry  = closes[0]
    qty    = cfg.initial / entry
    floor  = round(entry * cfg.stop_pct, 2)
    t_next = round(entry * cfg.trail_trigger, 2)
    l1     = round(floor * cfg.ladder1_pct, 2)
    l2     = round(floor * cfg.ladder2_pct, 2)
    cash   = 0.0
    l1_done      = False
    l2_done      = False
    half_stopped = False
    stopped      = False

    history: list[dict] = []   # daily snapshots {date, price, floor, t_next, value, cash}
    events:  list[dict] = []   # trade events with reasons

    def snap(i: int):
        p = closes[i]
        history.append({
            "date":   str(dates[i]),
            "price":  p,
            "floor":  round(floor, 2),
            "t_next": round(t_next, 2),
            "value":  round(qty * p, 2),
            "cash":   round(cash, 2),
            "total":  round(qty * p + cash, 2),
        })

    def evt(i: int, action: str, price: float, amount: float | None, reason: str):
        events.append({
            "date":   str(dates[i]),
            "action": action,
            "price":  round(price, 2),
            "amount": round(amount, 2) if amount is not None else None,
            "reason": reason,
        })

    # Day 0
    snap(0)
    evt(0, "BUY", entry, cfg.initial,
        f"Initial purchase: bought {qty:.4f} shares of TSLA at ${entry:,.2f}. "
        f"Stop floor set at ${floor:,.2f} ({(1-cfg.stop_pct)*100:.0f}% below entry). "
        f"Trailing starts when price reaches ${t_next:,.2f} (+{(cfg.trail_trigger-1)*100:.0f}%).")

    for i in range(1, len(dates)):
        if stopped:
            snap(i)
            continue

        close = closes[i]
        low   = lows[i]

        # ── Stop check ────────────────────────────────────────────────────────
        if low <= floor:
            sp = floor  # assume fill at the floor price
            if not half_stopped:
                sell_qty  = qty * cfg.stop_sell_pct
                proceeds  = round(sell_qty * sp, 2)
                qty      -= sell_qty
                cash     += proceeds
                half_stopped = True
                old_floor = floor
                floor     = round(sp * cfg.stop_pct, 2)
                t_next    = round(sp * cfg.trail_trigger, 2)
                evt(i, f"STOP — sold {int(cfg.stop_sell_pct*100)}%", sp, proceeds,
                    f"TSLA's low (${low:,.2f}) breached the floor (${old_floor:,.2f}). "
                    f"Sold {int(cfg.stop_sell_pct*100)}% of position ({sell_qty:.4f} shares) "
                    f"at ${sp:,.2f}, raising ${proceeds:,.2f} cash. "
                    f"Remaining {qty:.4f} shares stay in. "
                    f"New floor reset to ${floor:,.2f} — if price falls here again, the rest sells.")
            else:
                proceeds = round(qty * sp, 2)
                cash    += proceeds
                qty      = 0.0
                stopped  = True
                evt(i, "STOP — sold all", sp, proceeds,
                    f"TSLA's low (${low:,.2f}) breached the second floor (${floor:,.2f}). "
                    f"No more retries — sold the entire remaining position "
                    f"({qty:.4f} shares before this sale) at ${sp:,.2f}. "
                    f"${proceeds:,.2f} moved to cash. Bot is done for this run.")
            snap(i)
            continue

        # ── Trail ─────────────────────────────────────────────────────────────
        if close >= t_next:
            new_floor = round(close * cfg.trail_stop, 2)
            if new_floor > floor:
                old_floor = floor
                old_next  = t_next
                floor     = new_floor
                t_next    = round(close * cfg.trail_step, 2)
                evt(i, "TRAIL — floor raised", close, None,
                    f"TSLA closed at ${close:,.2f}, clearing the trail trigger (${old_next:,.2f}). "
                    f"Floor stepped up from ${old_floor:,.2f} → ${floor:,.2f} "
                    f"({(1-cfg.trail_stop)*100:.0f}% below current price), locking in gains. "
                    f"Next trail fires if price reaches ${t_next:,.2f}.")

        # ── Ladders ───────────────────────────────────────────────────────────
        if not l1_done and low <= l1 and cash >= 1.0:
            notional = min(cash, cfg.initial * 0.25)
            bought   = notional / l1
            qty     += bought
            cash    -= notional
            l1_done  = True
            evt(i, "LADDER 1 — bought more", l1, notional,
                f"TSLA's low (${low:,.2f}) reached ladder 1 at ${l1:,.2f} "
                f"({(1-cfg.ladder1_pct)*100:.1f}% below floor). "
                f"Bought {bought:.4f} more shares for ${notional:,.2f} from available cash. "
                f"Averaging down — lower cost basis improves recovery potential.")

        if not l2_done and low <= l2 and cash >= 1.0:
            notional = min(cash, cfg.initial * 0.25)
            bought   = notional / l2
            qty     += bought
            cash    -= notional
            l2_done  = True
            evt(i, "LADDER 2 — bought more", l2, notional,
                f"TSLA's low (${low:,.2f}) reached ladder 2 at ${l2:,.2f} "
                f"({(1-cfg.ladder2_pct)*100:.1f}% below floor). "
                f"Bought {bought:.4f} more shares for ${notional:,.2f} from available cash. "
                f"This is the second and final ladder buy.")

        snap(i)

    # ── Buy-and-hold baseline ─────────────────────────────────────────────────
    init_qty = cfg.initial / closes[0]
    bh = [round(init_qty * closes[i], 2) for i in range(len(dates))]

    # ── Summary ───────────────────────────────────────────────────────────────
    totals = [h["total"] for h in history]
    peak   = totals[0]; max_dd = 0.0
    for t in totals:
        peak   = max(peak, t)
        max_dd = max(max_dd, (peak - t) / peak)

    final    = totals[-1]
    bh_final = bh[-1]
    return {
        "dates":   [str(d) for d in dates],
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
            "n_stops":   sum(1 for e in events if "STOP"   in e["action"]),
            "n_trails":  sum(1 for e in events if "TRAIL"  in e["action"]),
            "n_ladders": sum(1 for e in events if "LADDER" in e["action"]),
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
  <title>TSLA Algo Sandbox</title>
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
    /* Sidebar */
    .sidebar{{display:grid;gap:0;}}
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
    /* Workspace */
    .workspace{{display:grid;gap:18px;}}
    .stat-band{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;}}
    .stat{{padding:16px 18px;border-radius:20px;background:linear-gradient(90deg,rgba(255,255,255,.03),rgba(255,255,255,.01));border:1px solid rgba(255,255,255,.06);}}
    .stat-label{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.12em;margin-bottom:10px;}}
    .stat-value{{font-size:28px;line-height:1;letter-spacing:-.03em;font-weight:700;}}
    .stat-sub{{font-size:12px;color:var(--muted);margin-top:4px;}}
    .chart-panel{{padding:20px;border-radius:24px;background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,.01));border:1px solid rgba(255,255,255,.06);}}
    .chart-panel h3{{margin:0 0 16px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;}}
    .chart-wrap{{position:relative;height:280px;}}
    /* Events */
    .events-panel{{padding:20px;border-radius:24px;background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,.01));border:1px solid rgba(255,255,255,.06);}}
    .events-panel h3{{margin:0 0 12px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;}}
    .tbl-wrap{{border-radius:14px;border:1px solid rgba(255,255,255,.05);overflow:hidden;}}
    table{{width:100%;border-collapse:collapse;font-size:12px;}}
    th,td{{padding:10px 14px;text-align:left;border-bottom:1px solid rgba(255,255,255,.04);}}
    th{{color:var(--muted);text-transform:uppercase;letter-spacing:.1em;font-size:11px;background:#13181b;}}
    td{{color:var(--text);}}
    tr:last-child td{{border-bottom:0;}}
    td.mono{{font-family:"IBM Plex Mono",monospace;}}
    td.reason{{color:var(--muted);font-size:11px;line-height:1.55;max-width:520px;}}
    .badge{{display:inline-flex;padding:3px 9px;border-radius:999px;font-size:11px;font-weight:600;white-space:nowrap;}}
    .badge.buy   {{color:var(--good);border:1px solid rgba(113,214,173,.35);background:rgba(113,214,173,.08);}}
    .badge.stop  {{color:var(--bad); border:1px solid rgba(255,143,112,.35);background:rgba(255,143,112,.08);}}
    .badge.trail {{color:#73b7ff;   border:1px solid rgba(115,183,255,.35);background:rgba(115,183,255,.08);}}
    .badge.ladder{{color:#c4a7ff;   border:1px solid rgba(196,167,255,.35);background:rgba(196,167,255,.08);}}
    .empty{{color:var(--muted);padding:40px;text-align:center;}}
    @media(max-width:900px){{.shell{{grid-template-columns:1fr}}.stat-band{{grid-template-columns:repeat(2,1fr)}}}}
  </style>
</head>
<body>
<div class="shell">

  <div class="topbar">
    <div class="brand">TSLA · Algo Sandbox</div>
    <div style="display:flex;gap:10px">
      <div class="pill" id="dataStatus">Loading…</div>
      <div class="pill">{ver}</div>
    </div>
  </div>

  <!-- ── Sidebar ─────────────────────────────────────────────────────────── -->
  <aside class="sidebar">
    <div class="panel">
      <h2>Simulation Parameters</h2>
      <div class="ctrl">

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Starting portfolio</span><span class="ctrl-val" id="v_initial">$1,000</span></div>
          <input type="range" id="s_initial" min="500" max="10000" step="500" value="1000">
          <div class="ctrl-desc">How much cash the algo starts with. Entire amount buys TSLA on day one.</div>
        </div>

        <hr class="div">

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Stop floor</span><span class="ctrl-val" id="v_stop_pct">95%</span></div>
          <input type="range" id="s_stop_pct" min="80" max="99" step="1" value="95">
          <div class="ctrl-desc">The safety net. If TSLA drops to this % of the entry price, a sell is triggered. At 95%, a $100 entry means a $95 floor — a 5% drawdown tolerance.</div>
        </div>

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Stop sell amount</span><span class="ctrl-val" id="v_stop_sell_pct">50%</span></div>
          <input type="range" id="s_stop_sell_pct" min="10" max="100" step="5" value="50">
          <div class="ctrl-desc">On the first stop, sell only this fraction of the position. The rest stays in with a new, lower floor. If price keeps falling and hits that floor too, everything remaining sells.</div>
        </div>

        <hr class="div">

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Trail trigger</span><span class="ctrl-val" id="v_trail_trigger">+10%</span></div>
          <input type="range" id="s_trail_trigger" min="101" max="150" step="1" value="110">
          <div class="ctrl-desc">Once TSLA gains this much from the entry price, the algo starts "trailing" — raising the floor as the price climbs, locking in profits.</div>
        </div>

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Trail step</span><span class="ctrl-val" id="v_trail_step">+5%</span></div>
          <input type="range" id="s_trail_step" min="101" max="130" step="1" value="105">
          <div class="ctrl-desc">After the first trail, every time price gains another X% the floor steps up again. Smaller = floor rises more aggressively; larger = more slack to ride volatility.</div>
        </div>

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Trail floor</span><span class="ctrl-val" id="v_trail_stop">95%</span></div>
          <input type="range" id="s_trail_stop" min="80" max="99" step="1" value="95">
          <div class="ctrl-desc">When the trail fires, the new floor is set to this % of the current price. At 95%, a $150 trigger sets the new floor at $142.50 — protecting 95% of that peak value.</div>
        </div>

        <hr class="div">

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Ladder 1</span><span class="ctrl-val" id="v_ladder1_pct">92.5% of floor</span></div>
          <input type="range" id="s_ladder1_pct" min="70" max="99" step="0.5" value="92.5">
          <div class="ctrl-desc">If TSLA drops to this % of the floor — and the algo has cash from a prior partial sell — it buys more shares at the lower price, improving the average cost.</div>
        </div>

        <div class="ctrl-group">
          <div class="ctrl-head"><span class="ctrl-name">Ladder 2</span><span class="ctrl-val" id="v_ladder2_pct">85% of floor</span></div>
          <input type="range" id="s_ladder2_pct" min="50" max="98" step="0.5" value="85">
          <div class="ctrl-desc">A second, deeper buy opportunity. Only triggers if cash is available and price drops even further below the floor.</div>
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

    <div class="chart-panel">
      <h3>TSLA Price · Floor · Trail Trigger</h3>
      <div class="chart-wrap"><canvas id="chartPrice"></canvas></div>
    </div>

    <div class="chart-panel">
      <h3>Portfolio Value — Algo vs Buy &amp; Hold</h3>
      <div class="chart-wrap"><canvas id="chartTotal"></canvas></div>
    </div>

    <div class="events-panel">
      <h3>Decision Log — every buy, sell, and trail explained</h3>
      <div class="tbl-wrap">
        <table>
          <thead>
            <tr><th>Date</th><th>Action</th><th>Price</th><th>Amount</th><th>Why</th></tr>
          </thead>
          <tbody id="eventsBody">
            <tr><td colspan="5" class="empty">Run a simulation to see decisions.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

  </main>
</div>

<script>
  let chartPrice = null;
  let chartTotal = null;

  // ── Sliders ──────────────────────────────────────────────────────────────────
  const sliders = [
    {{ id:"s_initial",       vid:"v_initial",       fmt: v => "$" + Number(v).toLocaleString() }},
    {{ id:"s_stop_pct",      vid:"v_stop_pct",      fmt: v => v + "%" }},
    {{ id:"s_stop_sell_pct", vid:"v_stop_sell_pct", fmt: v => v + "%" }},
    {{ id:"s_trail_trigger", vid:"v_trail_trigger", fmt: v => "+" + (v - 100) + "%" }},
    {{ id:"s_trail_step",    vid:"v_trail_step",    fmt: v => "+" + (v - 100) + "%" }},
    {{ id:"s_trail_stop",    vid:"v_trail_stop",    fmt: v => v + "%" }},
    {{ id:"s_ladder1_pct",   vid:"v_ladder1_pct",   fmt: v => v + "% of floor" }},
    {{ id:"s_ladder2_pct",   vid:"v_ladder2_pct",   fmt: v => v + "% of floor" }},
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
  function money(v, dec=0) {{
    if (v == null) return "—";
    return new Intl.NumberFormat(undefined, {{style:"currency",currency:"USD",maximumFractionDigits:dec}}).format(v);
  }}
  function signed(v) {{ return (v >= 0 ? "+" : "") + v.toFixed(1) + "%"; }}

  function badgeClass(action) {{
    if (action.startsWith("BUY") || action.startsWith("LADDER")) return action.startsWith("LADDER") ? "ladder" : "buy";
    if (action.startsWith("STOP")) return "stop";
    if (action.startsWith("TRAIL")) return "trail";
    return "buy";
  }}

  const chartBase = {{
    responsive:true, maintainAspectRatio:false,
    animation:{{duration:280}},
    interaction:{{mode:"index",intersect:false}},
    plugins:{{
      legend:{{labels:{{color:"#93a09f",font:{{size:12}}}}}},
      tooltip:{{backgroundColor:"#111417",borderColor:"rgba(255,255,255,.08)",borderWidth:1,titleColor:"#dfe8df",bodyColor:"#dfe8df",
        callbacks:{{label: ctx => ctx.dataset.label + ": " + money(ctx.parsed.y, 2)}}}}
    }},
    scales:{{
      x:{{type:"time",time:{{unit:"month",displayFormats:{{month:"MMM yy"}}}},ticks:{{color:"#93a09f",maxRotation:0}},grid:{{color:"rgba(255,255,255,.04)"}}}},
      y:{{ticks:{{color:"#93a09f",callback:v=>money(v)}},grid:{{color:"rgba(255,255,255,.05)"}}}}
    }}
  }};

  function renderPriceChart(dates, history) {{
    if (chartPrice) chartPrice.destroy();
    const ctx = document.getElementById("chartPrice").getContext("2d");
    chartPrice = new Chart(ctx, {{
      type:"line",
      data:{{
        labels: dates,
        datasets:[
          {{ label:"TSLA Price", data:history.map(h=>h.price), borderColor:"#d8b27a", borderWidth:2, pointRadius:0, tension:.15, fill:false, yAxisID:"y" }},
          {{ label:"Floor",      data:history.map(h=>h.floor), borderColor:"#ff8f70", borderWidth:1.5, borderDash:[6,4], pointRadius:0, stepped:"before", fill:false, yAxisID:"y" }},
          {{ label:"Trail next", data:history.map(h=>h.t_next), borderColor:"#71d6ad", borderWidth:1.2, borderDash:[3,5], pointRadius:0, fill:false, yAxisID:"y" }},
        ]
      }},
      options:chartBase,
    }});
  }}

  function renderTotalChart(dates, history, bh) {{
    if (chartTotal) chartTotal.destroy();
    const ctx = document.getElementById("chartTotal").getContext("2d");
    chartTotal = new Chart(ctx, {{
      type:"line",
      data:{{
        labels:dates,
        datasets:[
          {{ label:"Algo", data:history.map(h=>h.total), borderColor:"#d8b27a", borderWidth:2.5, pointRadius:0, tension:.15, fill:false }},
          {{ label:"Buy & Hold", data:bh, borderColor:"rgba(147,160,159,.45)", borderWidth:1.5, borderDash:[5,4], pointRadius:0, tension:.15, fill:false }},
        ]
      }},
      options:chartBase,
    }});
  }}

  function renderEvents(events) {{
    const tbody = document.getElementById("eventsBody");
    if (!events.length) {{
      tbody.innerHTML = `<tr><td colspan="5" class="empty">No events.</td></tr>`;
      return;
    }}
    tbody.innerHTML = events.map(e => `
      <tr>
        <td class="mono" style="white-space:nowrap">${{e.date}}</td>
        <td><span class="badge ${{badgeClass(e.action)}}">${{e.action}}</span></td>
        <td class="mono">${{e.price != null ? money(e.price,2) : "—"}}</td>
        <td class="mono">${{e.amount != null ? money(e.amount,2) : "—"}}</td>
        <td class="reason">${{e.reason}}</td>
      </tr>`).join("");
  }}

  function renderSummary(s) {{
    const col = v => v >= 0 ? "var(--good)" : "var(--bad)";
    const fin = document.getElementById("s_final");
    fin.textContent = money(s.final);
    fin.style.color = col(s.return_pct);
    document.getElementById("s_return").textContent = signed(s.return_pct);
    document.getElementById("s_dd").textContent = "-" + s.max_dd_pct.toFixed(1) + "%";
    const bh = document.getElementById("s_bh");
    bh.textContent = money(s.bh_final);
    bh.style.color = col(s.bh_return_pct);
    document.getElementById("s_bh_return").textContent = signed(s.bh_return_pct);
    document.getElementById("s_nevents").textContent = s.n_stops + s.n_trails + s.n_ladders;
    document.getElementById("s_events_sub").textContent =
      s.n_stops + " stops · " + s.n_trails + " trails · " + s.n_ladders + " ladders";
  }}

  // ── Run ───────────────────────────────────────────────────────────────────────
  document.getElementById("runBtn").addEventListener("click", async () => {{
    const btn = document.getElementById("runBtn");
    btn.disabled = true;
    btn.textContent = "Running…";
    try {{
      const res  = await fetch("/api/simulate", {{
        method:"POST",
        headers:{{"Content-Type":"application/json"}},
        body:JSON.stringify(getConfig()),
      }});
      const data = await res.json();
      if (!data.ok) throw new Error(data.message);
      const r = data.result;
      renderPriceChart(r.dates, r.history);
      renderTotalChart(r.dates, r.history, r.bh);
      renderEvents(r.events);
      renderSummary(r.summary);
    }} catch(err) {{
      alert("Error: " + err.message);
    }} finally {{
      btn.disabled = false;
      btn.textContent = "Run Simulation";
    }}
  }});

  // ── Data status ───────────────────────────────────────────────────────────────
  fetch("/api/data-status").then(r=>r.json()).then(d=>{{
    document.getElementById("dataStatus").textContent = d.ready
      ? "TSLA · " + d.days + " trading days · " + d.start + " → " + d.end
      : "Click Run to fetch TSLA data";
  }}).catch(()=>{{
    document.getElementById("dataStatus").textContent = "Click Run to fetch TSLA data";
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
                dates = d["dates"]
                json_resp(self, {"ok": True, "ready": True,
                                 "days": len(dates),
                                 "start": str(dates[0]),
                                 "end":   str(dates[-1])})
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
        port = int(sys.argv[sys.argv.index("--port") + 1])
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"TSLA sandbox → http://localhost:{port}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.4, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    threading.Thread(target=load_data, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
