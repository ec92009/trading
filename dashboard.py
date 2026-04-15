"""
Local web dashboard for trading bot status.
Serves an HTML page with Chart.js charts at http://localhost:8080.
Click Refresh to pull fresh data from Alpaca + bot.log.

Usage:
    python3 dashboard.py          # starts server, opens browser
    python3 dashboard.py --no-browser
"""

import os, re, json, sys, threading, webbrowser
from datetime import datetime, date
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

PORT = 8080

# ── Colors per asset index ────────────────────────────────────────────────────

COLORS = ["#4A90D9", "#F7931A", "#2ecc71", "#e74c3c",
          "#9b59b6", "#1abc9c", "#f39c12", "#e67e22"]

# ── Data fetching ─────────────────────────────────────────────────────────────

def load_bots() -> list[dict]:
    text = (HERE / "bot.py").read_text()
    entries = re.findall(
        r'BotConfig\s*\(\s*symbol\s*=\s*"([^"]+)"\s*,\s*asset_class\s*=\s*"([^"]+)"',
        text,
    )
    return [
        {"symbol": sym, "asset_class": ac, "color": COLORS[i % len(COLORS)]}
        for i, (sym, ac) in enumerate(entries)
    ]

def parse_history(symbol: str) -> tuple:
    tag   = symbol.replace("/", "")
    today = date.today()
    times, prices, floors = [], [], []
    entry = None

    lines = (HERE / "bot.log").read_text().splitlines()
    last_start = 0
    for i, line in enumerate(lines):
        if f"[{tag}]" in line and "BOT STARTED" in line:
            last_start = i

    for line in lines[last_start:]:
        if f"[{tag}]" not in line:
            continue
        m_ts = re.match(r'(\d{2}:\d{2}:\d{2})', line)
        if not m_ts:
            continue
        dt = datetime.combine(today, datetime.strptime(m_ts.group(1), "%H:%M:%S").time())

        if "Entry" in line and entry is None:
            m = re.search(r'\$([0-9,]+\.?\d*)', line)
            if m:
                entry = float(m.group(1).replace(",", ""))
            continue

        mp = re.search(r'price=\$([0-9,]+\.?\d*)', line)
        mf = re.search(r'floor=\$([0-9,]+\.?\d*)', line)
        if mp and mf:
            times.append(dt.strftime("%Y-%m-%dT%H:%M:%S"))
            prices.append(float(mp.group(1).replace(",", "")))
            floors.append(float(mf.group(1).replace(",", "")))

    return times, prices, floors, entry

def fetch_data() -> dict:
    from alpaca.trading.client import TradingClient
    from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest

    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    trading     = TradingClient(api_key=key, secret_key=secret, paper=True)
    stock_data  = StockHistoricalDataClient(api_key=key, secret_key=secret)
    crypto_data = CryptoHistoricalDataClient(api_key=key, secret_key=secret)

    account   = trading.get_account()
    positions = {p.symbol: p for p in trading.get_all_positions()}
    bots      = load_bots()

    assets = {}
    for a in bots:
        sym = a["symbol"]
        tag = sym.replace("/", "")

        # Live price
        try:
            if a["asset_class"] == "crypto":
                q = crypto_data.get_crypto_latest_quote(
                    CryptoLatestQuoteRequest(symbol_or_symbols=sym))[sym]
            else:
                q = stock_data.get_stock_latest_quote(
                    StockLatestQuoteRequest(symbol_or_symbols=sym))[sym]
            ask, bid = float(q.ask_price or 0), float(q.bid_price or 0)
            live = (ask + bid) / 2 if (ask and bid) else ask or bid
        except Exception:
            live = 0.0

        pos       = positions.get(tag)
        avg_entry = float(pos.avg_entry_price) if pos else None
        mkt_val   = float(pos.market_value)    if pos else 0.0
        pl        = float(pos.unrealized_pl)   if pos else 0.0
        pl_pct    = float(pos.unrealized_plpc) * 100 if pos else 0.0
        qty       = float(pos.qty)             if pos else 0.0

        times, prices, floors, log_entry = parse_history(sym)
        entry = avg_entry or log_entry or live

        # Append live point
        if times:
            now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            times.append(now)
            prices.append(live)
            floors.append(floors[-1] if floors else entry * 0.95)

        assets[sym] = {
            "color":     a["color"],
            "times":     times,
            "prices":    prices,
            "floors":    floors,
            "entry":     entry,
            "live":      live,
            "mkt_val":   mkt_val,
            "pl":        pl,
            "pl_pct":    pl_pct,
            "qty":       qty,
        }

    return {
        "assets":    assets,
        "portfolio": float(account.portfolio_value),
        "cash":      float(account.cash),
        "total_pl":  sum(float(p.unrealized_pl) for p in positions.values()),
        "updated":   datetime.now().strftime("%H:%M:%S"),
    }

# ── HTML generation ───────────────────────────────────────────────────────────

def build_html(data: dict) -> str:
    assets    = data["assets"]
    n         = len(assets)
    ncols     = min(n, 3)
    col_pct   = 100 // ncols

    charts_js = []
    cards_html = []

    for sym, a in assets.items():
        pl_color = "#2ecc71" if a["pl"] >= 0 else "#e74c3c"
        pl_sign  = "+" if a["pl"] >= 0 else ""
        live_fmt = f"${a['live']:,.2f}" if a["live"] < 10000 else f"${a['live']:,.0f}"
        entry_fmt = f"${a['entry']:,.2f}" if a["entry"] and a["entry"] < 10000 else f"${a['entry']:,.0f}" if a["entry"] else "—"

        safe = sym.replace("/", "_")
        cards_html.append(f"""
        <div class="card">
          <div class="card-header">
            <span class="sym">{sym}</span>
            <span class="live" style="color:{a['color']}">{live_fmt}</span>
            <span class="pl" style="color:{pl_color}">{pl_sign}{a['pl']:.2f} ({pl_sign}{a['pl_pct']:.2f}%)</span>
          </div>
          <div class="sub">Entry {entry_fmt} &nbsp;|&nbsp; ${a['mkt_val']:.2f} &nbsp;|&nbsp; {a['qty']:.6f}</div>
          <div class="chart-wrap"><canvas id="chart-{safe}"></canvas></div>
        </div>""")

        # Y axis centering
        entry = a["entry"] or a["live"] or 1
        all_vals = a["prices"] + a["floors"]
        if all_vals:
            half = max(abs(v - entry) for v in all_vals) * 1.35 or entry * 0.10
        else:
            half = entry * 0.10
        y_min = round(entry - half, 2)
        y_max = round(entry + half, 2)

        charts_js.append(f"""
  new Chart(document.getElementById('chart-{safe}'), {{
    type: 'line',
    data: {{
      labels: {json.dumps(a['times'])},
      datasets: [
        {{
          label: 'Price',
          data: {json.dumps(a['prices'])},
          borderColor: '{a['color']}',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.2,
          fill: false,
          order: 1,
        }},
        {{
          label: 'Floor',
          data: {json.dumps(a['floors'])},
          borderColor: '#e74c3c',
          borderWidth: 1.5,
          borderDash: [5, 4],
          pointRadius: 0,
          tension: 0,
          stepped: 'before',
          fill: false,
          order: 2,
        }}
      ]
    }},
    options: {{
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          backgroundColor: '#1a1d27',
          borderColor: '#333',
          borderWidth: 1,
          titleColor: '#aaa',
          bodyColor: '#fff',
          callbacks: {{
            label: ctx => ctx.dataset.label + ': $' + ctx.parsed.y.toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}})
          }}
        }}
      }},
      scales: {{
        x: {{
          type: 'time',
          time: {{ unit: 'minute', displayFormats: {{ minute: 'HH:mm' }} }},
          ticks: {{ color: '#666', maxTicksLimit: 6, maxRotation: 0 }},
          grid: {{ color: '#1e2130' }},
        }},
        y: {{
          min: {y_min},
          max: {y_max},
          ticks: {{
            color: '#666',
            maxTicksLimit: 5,
            callback: v => '$' + v.toLocaleString(undefined, {{maximumFractionDigits: 2}})
          }},
          grid: {{ color: '#1e2130' }},
        }}
      }}
    }}
  }});""")

    total_pl_color = "#2ecc71" if data["total_pl"] >= 0 else "#e74c3c"
    total_sign     = "+" if data["total_pl"] >= 0 else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trading Bot Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{
      height: 100%;
      background: #0f1117; color: #ccc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 13px;
    }}
    body {{
      display: flex; flex-direction: column;
      padding: 12px; height: 100vh; overflow: hidden;
    }}
    header {{
      flex-shrink: 0;
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 10px; padding-bottom: 10px;
      border-bottom: 1px solid #2a2d3a;
    }}
    h1 {{ font-size: 16px; color: #fff; font-weight: 600; letter-spacing: .5px; }}
    .meta {{ color: #555; font-size: 12px; }}
    .meta span {{ margin-left: 16px; }}
    .meta .pl {{ color: {total_pl_color}; font-weight: 600; }}
    button {{
      background: #1a3a5c; color: #4A90D9; border: 1px solid #4A90D9;
      padding: 6px 18px; border-radius: 4px; font-size: 13px; cursor: pointer;
      transition: background .15s;
    }}
    button:hover {{ background: #4A90D9; color: #fff; }}
    .grid {{
      flex: 1; min-height: 0;
      display: flex; flex-wrap: wrap; gap: 10px;
      align-content: stretch;
    }}
    .card {{
      background: #1a1d27; border: 1px solid #2a2d3a; border-radius: 6px;
      padding: 10px;
      flex: 1 1 calc({100 // ncols}% - 10px);
      min-width: 220px;
      display: flex; flex-direction: column;
      min-height: 0;
    }}
    .card-header {{
      flex-shrink: 0;
      display: flex; align-items: baseline; gap: 10px; margin-bottom: 2px;
    }}
    .sym {{ font-size: 15px; font-weight: 700; color: #fff; }}
    .live {{ font-size: 14px; font-weight: 600; }}
    .pl  {{ font-size: 12px; margin-left: auto; font-weight: 600; }}
    .sub {{ flex-shrink: 0; color: #555; font-size: 11px; margin-bottom: 6px; }}
    .chart-wrap {{
      flex: 1; min-height: 0; position: relative;
    }}
    canvas {{ position: absolute; inset: 0; }}
    footer {{
      flex-shrink: 0;
      margin-top: 10px; text-align: center;
      color: #333; font-size: 11px; padding-top: 10px;
      border-top: 1px solid #1e2130;
    }}
  </style>
</head>
<body>
<header>
  <div>
    <h1>Trading Bot — Live Status</h1>
    <div class="meta">
      <span>Portfolio <strong style="color:#fff">${data['portfolio']:,.2f}</strong></span>
      <span>Cash <strong style="color:#fff">${data['cash']:,.2f}</strong></span>
      <span class="pl">P&amp;L {total_sign}${data['total_pl']:.2f}</span>
      <span>Updated {data['updated']}</span>
    </div>
  </div>
  <button onclick="location.href='/refresh'">&#8635; Refresh</button>
</header>

<div class="grid">
  {''.join(cards_html)}
</div>

<footer>Trading Bot &mdash; paper account &mdash; floor shown as dashed red line</footer>

<script>
{''.join(charts_js)}
</script>
</body>
</html>"""

# ── HTTP server ───────────────────────────────────────────────────────────────

_html_cache = ""

def regenerate():
    global _html_cache
    print("Fetching data…", end=" ", flush=True)
    try:
        data = fetch_data()
        _html_cache = build_html(data)
        print("done.")
    except Exception as e:
        print(f"ERROR: {e}")

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass   # suppress request logs

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/refresh":
            regenerate()
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
        elif path == "/":
            body = _html_cache.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    regenerate()
    server = HTTPServer(("localhost", PORT), Handler)
    print(f"Dashboard → http://localhost:{PORT}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
