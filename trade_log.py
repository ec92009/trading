"""
TSV trade log — persistent record of every order the bot places.

On restart, the bot checks this log before placing new orders to avoid
cancel → re-queue cycles for orders that are already pending on Alpaca.

Schema (trades.tsv):
    symbol | order_id | side | notional | status | submitted_at | filled_at | avg_price
"""

import csv
import threading
from datetime import datetime, timezone
from pathlib import Path

FIELDS = [
    "symbol", "order_id", "side", "notional",
    "status", "submitted_at", "filled_at", "avg_price",
]
LOG_PATH  = Path(__file__).parent / "trades.tsv"
_lock     = threading.Lock()

# ── Internal I/O ──────────────────────────────────────────────────────────────

def _read() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    with open(LOG_PATH, newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))

def _write(rows: list[dict]):
    with open(LOG_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Public API ────────────────────────────────────────────────────────────────

def log_order(symbol: str, order_id: str, side: str, notional: float):
    """Append a new order as 'pending'."""
    with _lock:
        rows = _read()
        rows.append({
            "symbol":       symbol,
            "order_id":     str(order_id),
            "side":         side,
            "notional":     f"{notional:.2f}",
            "status":       "pending",
            "submitted_at": _now(),
            "filled_at":    "",
            "avg_price":    "",
        })
        _write(rows)

def update_order(order_id: str, status: str, avg_price: float = None):
    """Update the status of an existing order. Marks fill time if status='filled'."""
    with _lock:
        rows = _read()
        for row in rows:
            if row["order_id"] == str(order_id):
                row["status"] = status
                if status == "filled":
                    row["filled_at"] = _now()
                if avg_price is not None:
                    row["avg_price"] = f"{avg_price:.4f}"
                break
        _write(rows)

def get_pending_buy(symbol: str) -> dict | None:
    """Return the most recent pending BUY row for a symbol, or None."""
    with _lock:
        rows = _read()
    for row in reversed(rows):
        if (row["symbol"] == symbol
                and row["side"] == "BUY"
                and row["status"] == "pending"):
            return row
    return None

def all_rows() -> list[dict]:
    """Return all rows (for inspection / status display)."""
    with _lock:
        return _read()
