"""
Visual portfolio status dashboard.
Shows current price, entry, floor, and trail trigger for each active bot position.
"""

import os
import re
import sys
import matplotlib
matplotlib.use("MacOSX" if not os.environ.get("SAVE_ONLY") else "Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from pathlib import Path
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest

load_dotenv(Path(__file__).parent / ".env")

# ── Clients ───────────────────────────────────────────────────────────────────

_key    = os.getenv("ALPACA_API_KEY")
_secret = os.getenv("ALPACA_SECRET_KEY")
trading     = TradingClient(api_key=_key, secret_key=_secret, paper=True)
stock_data  = StockHistoricalDataClient(api_key=_key, secret_key=_secret)
crypto_data = CryptoHistoricalDataClient(api_key=_key, secret_key=_secret)

# ── Data ──────────────────────────────────────────────────────────────────────

def get_price(symbol, asset_class):
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

def parse_bot_state(symbol):
    """Extract latest entry, floor, trail_next from bot.log."""
    log = Path(__file__).parent / "bot.log"
    tag = symbol.replace("/", "")
    entry = floor = trail = None
    with open(log) as f:
        for line in f:
            if f"[{tag}]" not in line:
                continue
            if "Entry" in line:
                m = re.search(r'\$([0-9,]+\.?\d*)', line)
                if m: entry = float(m.group(1).replace(",", ""))
            elif "Stop loss" in line:
                m = re.search(r'\$([0-9,]+\.?\d*)', line)
                if m: floor = float(m.group(1).replace(",", ""))
            elif "Trail trigger" in line:
                m = re.search(r'\$([0-9,]+\.?\d*)', line)
                if m: trail = float(m.group(1).replace(",", ""))
    return entry, floor, trail

ASSETS = [
    {"symbol": "AAPL",    "asset_class": "stock",  "label": "AAPL",    "color": "#4A90D9"},
    {"symbol": "BTC/USD", "asset_class": "crypto", "label": "BTC/USD", "color": "#F7931A"},
]

positions = {p.symbol: p for p in trading.get_all_positions()}
account   = trading.get_account()

# ── Build data ────────────────────────────────────────────────────────────────

rows = []
for a in ASSETS:
    sym   = a["symbol"]
    tag   = sym.replace("/", "")
    pos   = positions.get(tag)
    entry, floor, trail = parse_bot_state(sym)
    price = get_price(sym, a["asset_class"])

    avg   = float(pos.avg_entry_price) if pos else entry or price
    mkt   = float(pos.market_value)    if pos else 0.0
    pl    = float(pos.unrealized_pl)   if pos else 0.0
    qty   = float(pos.qty)             if pos else 0.0

    rows.append({
        "label":  a["label"],
        "color":  a["color"],
        "price":  price,
        "entry":  avg,
        "floor":  floor or avg * 0.95,
        "trail":  trail or avg * 1.10,
        "mkt":    mkt,
        "pl":     pl,
        "qty":    qty,
    })

# ── Plot ──────────────────────────────────────────────────────────────────────

fig, axes = plt.subplots(1, len(rows), figsize=(13, 6))
fig.patch.set_facecolor("#0f1117")

if len(rows) == 1:
    axes = [axes]

for ax, r in zip(axes, rows):
    ax.set_facecolor("#1a1d27")

    floor  = r["floor"]
    trail  = r["trail"]
    price  = r["price"]
    entry  = r["entry"]

    # vertical range: floor - 5% to trail + 5%
    lo = floor  * 0.95
    hi = trail  * 1.05
    span = hi - lo

    def pct(v): return (v - lo) / span   # 0–1 within plot range

    # ── Background zones ──────────────────────────────────────────────────────
    ax.barh(0, pct(floor) - pct(lo),  left=pct(lo),    height=0.6,
            color="#c0392b", alpha=0.25, zorder=1)   # danger zone
    ax.barh(0, pct(trail) - pct(floor), left=pct(floor), height=0.6,
            color="#27ae60", alpha=0.15, zorder=1)   # safe zone
    ax.barh(0, pct(hi)    - pct(trail), left=pct(trail), height=0.6,
            color="#2980b9", alpha=0.2,  zorder=1)   # trail zone

    # ── Key level markers ─────────────────────────────────────────────────────
    for val, lbl, clr, ls in [
        (floor,  "FLOOR",   "#e74c3c", "--"),
        (entry,  "ENTRY",   "#bdc3c7", ":"),
        (trail,  "TRAIL ▲", "#3498db", "--"),
    ]:
        ax.axvline(pct(val), color=clr, linestyle=ls, linewidth=1.5, alpha=0.8, zorder=2)
        ax.text(pct(val), 0.42, lbl, color=clr, fontsize=7.5, ha="center",
                fontweight="bold", va="bottom")
        fmt = f"${val:,.0f}" if val > 999 else f"${val:.2f}"
        ax.text(pct(val), -0.42, fmt, color=clr, fontsize=7.5, ha="center", va="top")

    # ── Current price marker ──────────────────────────────────────────────────
    ax.axvline(pct(price), color=r["color"], linewidth=3, zorder=4)
    pfmt = f"${price:,.0f}" if price > 999 else f"${price:.2f}"
    ax.text(pct(price), 0.55, pfmt, color=r["color"], fontsize=11,
            ha="center", fontweight="bold", va="bottom")
    ax.text(pct(price), -0.55, "NOW", color=r["color"], fontsize=8,
            ha="center", va="top", fontweight="bold")

    # ── P&L badge ─────────────────────────────────────────────────────────────
    pl_color = "#2ecc71" if r["pl"] >= 0 else "#e74c3c"
    pl_sign  = "+" if r["pl"] >= 0 else ""
    pct_chg  = (price - entry) / entry * 100
    ax.text(0.98, 0.97,
            f"{pl_sign}${r['pl']:.2f}  ({pl_sign}{pct_chg:.2f}%)",
            transform=ax.transAxes, color=pl_color, fontsize=9,
            ha="right", va="top", fontweight="bold",
            bbox=dict(facecolor="#1a1d27", edgecolor=pl_color, boxstyle="round,pad=0.3"))

    # ── Market value badge ────────────────────────────────────────────────────
    ax.text(0.02, 0.97,
            f"${r['mkt']:.2f}  ({r['qty']:.6f})",
            transform=ax.transAxes, color="#ecf0f1", fontsize=8,
            ha="left", va="top",
            bbox=dict(facecolor="#1a1d27", edgecolor="#555", boxstyle="round,pad=0.3"))

    # ── Styling ───────────────────────────────────────────────────────────────
    ax.set_title(r["label"], color="white", fontsize=16, fontweight="bold", pad=12)
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.8, 0.8)
    ax.axis("off")

# ── Account summary footer ────────────────────────────────────────────────────
total_pl  = sum(r["pl"]  for r in rows)
total_mkt = sum(r["mkt"] for r in rows)
cash      = float(account.cash)
portfolio = float(account.portfolio_value)
pl_color  = "#2ecc71" if total_pl >= 0 else "#e74c3c"
pl_sign   = "+" if total_pl >= 0 else ""

fig.text(0.5, 0.04,
         f"Portfolio: ${portfolio:,.2f}   |   "
         f"Positions: ${total_mkt:.2f}   |   "
         f"Cash: ${cash:,.2f}   |   "
         f"P&L: {pl_sign}${total_pl:.2f}",
         ha="center", color="#aaaaaa", fontsize=10,
         bbox=dict(facecolor="#0f1117", edgecolor="#333", boxstyle="round,pad=0.5"))

fig.suptitle("Trading Bot — Live Status", color="white", fontsize=14,
             fontweight="bold", y=0.97)
plt.tight_layout(rect=[0, 0.09, 1, 0.93])
if os.environ.get("SAVE_ONLY"):
    out = Path(__file__).parent / "status.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor="#0f1117")
    print(f"Saved to {out}")
else:
    plt.show()
