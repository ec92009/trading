"""
Trading bot status dashboard.
One subplot per asset showing price history vs time,
with floor as a step function. Y axis centered on entry price.
"""

import os
import re
import sys
import matplotlib
matplotlib.use("MacOSX" if not os.environ.get("SAVE_ONLY") else "Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, date
from pathlib import Path
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

_key    = os.getenv("ALPACA_API_KEY")
_secret = os.getenv("ALPACA_SECRET_KEY")
trading     = TradingClient(api_key=_key, secret_key=_secret, paper=True)
stock_data  = StockHistoricalDataClient(api_key=_key, secret_key=_secret)
crypto_data = CryptoHistoricalDataClient(api_key=_key, secret_key=_secret)

# ── Load BOTS list dynamically from bot.py ────────────────────────────────────

def _load_bots() -> list[dict]:
    """Parse BOTS from bot.py so status.py never needs manual updates."""
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("bot", HERE / "bot.py")
    mod  = importlib.util.load_from_spec = None  # avoid running __main__
    # Simple regex parse — avoids executing bot.py
    text = (HERE / "bot.py").read_text()
    entries = re.findall(
        r'BotConfig\s*\(\s*symbol\s*=\s*"([^"]+)"\s*,\s*asset_class\s*=\s*"([^"]+)"',
        text,
    )
    colors = [
        "#4A90D9", "#F7931A", "#2ecc71", "#e74c3c",
        "#9b59b6", "#1abc9c", "#f39c12", "#e67e22",
    ]
    return [
        {"symbol": sym, "asset_class": ac, "color": colors[i % len(colors)]}
        for i, (sym, ac) in enumerate(entries)
    ]

ASSETS = _load_bots()

# ── Log parsing ───────────────────────────────────────────────────────────────

def parse_history(symbol: str):
    """
    Parse bot.log for the most recent session of this symbol.
    Returns (times, prices, floors, entry_price).
    """
    tag   = symbol.replace("/", "")
    today = date.today()
    times, prices, floors = [], [], []
    entry = None

    with open(HERE / "bot.log") as f:
        lines = f.readlines()

    # Find the start of the most recent session for this symbol
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

        if "Entry" in line and ":" in line and entry is None:
            m = re.search(r'\$([0-9,]+\.?\d*)', line)
            if m:
                entry = float(m.group(1).replace(",", ""))
            continue

        m_price = re.search(r'price=\$([0-9,]+\.?\d*)', line)
        m_floor = re.search(r'floor=\$([0-9,]+\.?\d*)', line)
        if m_price and m_floor:
            times.append(dt)
            prices.append(float(m_price.group(1).replace(",", "")))
            floors.append(float(m_floor.group(1).replace(",", "")))

    return times, prices, floors, entry

def get_live_price(symbol, asset_class):
    if asset_class == "crypto":
        q = crypto_data.get_crypto_latest_quote(
            CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
        )[symbol]
    else:
        q = stock_data.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol)
        )[symbol]
    ask, bid = float(q.ask_price or 0), float(q.bid_price or 0)
    return (ask + bid) / 2 if (ask and bid) else ask or bid

# ── Build chart ───────────────────────────────────────────────────────────────

positions = {p.symbol: p for p in trading.get_all_positions()}
account   = trading.get_account()

total  = len(ASSETS)
ncols  = min(total, 4)
nrows  = (total + ncols - 1) // ncols       # ceil division
fig, axes = plt.subplots(nrows, ncols,
                         figsize=(6 * ncols, 5 * nrows),
                         sharey=False, squeeze=False)
fig.patch.set_facecolor("#0f1117")
axes_flat = [axes[r][c] for r in range(nrows) for c in range(ncols)]

# Hide any unused subplots in the last row
for ax in axes_flat[total:]:
    ax.set_visible(False)

for ax, a in zip(axes_flat, ASSETS):
    sym        = a["symbol"]
    tag        = sym.replace("/", "")
    color      = a["color"]
    times, prices, floors, entry = parse_history(sym)
    live_price = get_live_price(sym, a["asset_class"])
    pos        = positions.get(tag)
    avg_entry  = float(pos.avg_entry_price) if pos else entry

    # Append live price to history so chart extends to now
    if times:
        times.append(datetime.now())
        prices.append(live_price)
        floors.append(floors[-1] if floors else (avg_entry * 0.95 if avg_entry else live_price * 0.95))

    ax.set_facecolor("#1a1d27")

    if not times or avg_entry is None:
        ax.text(0.5, 0.5, "No data yet", transform=ax.transAxes,
                color="#888", ha="center", va="center", fontsize=12)
        ax.set_title(sym, color="white", fontsize=14, fontweight="bold")
        continue

    # ── Y axis: centered on entry price ──────────────────────────────────────
    all_vals  = prices + floors + [avg_entry]
    half_span = max(abs(v - avg_entry) for v in all_vals) * 1.35 or avg_entry * 0.10
    y_lo      = avg_entry - half_span
    y_hi      = avg_entry + half_span
    ax.set_ylim(y_lo, y_hi)

    # ── Entry center line ─────────────────────────────────────────────────────
    ax.axhline(avg_entry, color="#888888", linewidth=0.8, linestyle=":", alpha=0.7)

    # ── Floor step function ───────────────────────────────────────────────────
    if floors:
        ax.step(times, floors, where="post", color="#e74c3c",
                linewidth=1.8, linestyle="--", alpha=0.9, label="Floor", zorder=3)
        # Label the current floor value
        current_floor = floors[-1]
        ax.annotate(
            f"  FLOOR\n  ${current_floor:,.2f}",
            xy=(times[-1], current_floor),
            color="#e74c3c", fontsize=8, va="center",
            fontweight="bold",
        )

    # ── Price line ────────────────────────────────────────────────────────────
    ax.plot(times, prices, color=color, linewidth=2, zorder=4, label="Price")

    # Mark current price
    ax.plot(times[-1], live_price, "o", color=color, markersize=7, zorder=5)
    price_fmt = f"${live_price:,.2f}" if live_price < 10000 else f"${live_price:,.0f}"
    ax.annotate(
        f"  {price_fmt}",
        xy=(times[-1], live_price),
        color=color, fontsize=9, va="center", fontweight="bold",
    )

    # ── Y axis: dollar labels ─────────────────────────────────────────────────
    tick_count = 6
    import numpy as np
    ticks = np.linspace(y_lo, y_hi, tick_count)
    ax.set_yticks(ticks)
    if avg_entry >= 1000:
        ax.set_yticklabels([f"${v:,.0f}" for v in ticks], color="#aaaaaa", fontsize=8)
    else:
        ax.set_yticklabels([f"${v:.2f}" for v in ticks], color="#aaaaaa", fontsize=8)
    ax.yaxis.set_label_position("left")

    # Mark entry on y axis
    ax.axhline(avg_entry, color="#555", linewidth=0.5)
    ax.annotate(
        f"ENTRY ${avg_entry:,.2f}" if avg_entry >= 1000 else f"ENTRY ${avg_entry:.2f}",
        xy=(times[0], avg_entry),
        color="#888888", fontsize=7.5, va="bottom", ha="left", alpha=0.9,
    )

    # ── X axis: time ──────────────────────────────────────────────────────────
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right",
             color="#aaaaaa", fontsize=8)

    # ── P&L badge ─────────────────────────────────────────────────────────────
    pl        = float(pos.unrealized_pl) if pos else 0.0
    pct_chg   = (live_price - avg_entry) / avg_entry * 100
    pl_sign   = "+" if pl >= 0 else ""
    pl_color  = "#2ecc71" if pl >= 0 else "#e74c3c"
    ax.text(0.98, 0.98,
            f"{pl_sign}${pl:.2f}  ({pl_sign}{pct_chg:.2f}%)",
            transform=ax.transAxes, color=pl_color, fontsize=9,
            ha="right", va="top", fontweight="bold",
            bbox=dict(facecolor="#1a1d27", edgecolor=pl_color,
                      boxstyle="round,pad=0.3", alpha=0.9))

    # ── Styling ───────────────────────────────────────────────────────────────
    ax.set_title(sym, color="white", fontsize=14, fontweight="bold", pad=10)
    ax.tick_params(axis="x", colors="#aaaaaa")
    ax.tick_params(axis="y", colors="#aaaaaa")
    for spine in ax.spines.values():
        spine.set_edgecolor("#333")
    ax.grid(axis="y", color="#2a2d3a", linewidth=0.5, alpha=0.7)

# ── Footer ────────────────────────────────────────────────────────────────────

portfolio = float(account.portfolio_value)
cash      = float(account.cash)
total_pl  = sum(
    float(p.unrealized_pl) for p in trading.get_all_positions()
)
pl_sign   = "+" if total_pl >= 0 else ""

fig.text(0.5, 0.01,
         f"Portfolio: ${portfolio:,.2f}   |   Cash: ${cash:,.2f}   |   "
         f"Total P&L: {pl_sign}${total_pl:.2f}   |   "
         f"Updated: {datetime.now().strftime('%H:%M:%S')}",
         ha="center", color="#888", fontsize=9,
         bbox=dict(facecolor="#0f1117", edgecolor="#333",
                   boxstyle="round,pad=0.4"))

fig.suptitle("Trading Bot — Live Status", color="white",
             fontsize=13, fontweight="bold", y=0.99)
plt.tight_layout(rect=[0, 0.06, 1, 0.97])

if os.environ.get("SAVE_ONLY"):
    out = HERE / "status.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor="#0f1117")
    print(f"Saved to {out}")
else:
    plt.show()
