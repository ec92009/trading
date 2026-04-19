"""
TSV trade log — persistent record of every order the bot places.

On restart, the bot checks this log before placing new orders to avoid
cancel → re-queue cycles for orders that are already pending on Alpaca.

Schema (trades.tsv):
    symbol | order_id | side | notional | status | alpaca_request | rationale |
    submitted_at | executed_at | filled_at | avg_price
"""

import csv
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

FIELDS = [
    "symbol", "order_id", "side", "notional",
    "status", "alpaca_request", "rationale",
    "submitted_at", "executed_at", "filled_at", "avg_price",
]


def _log_path() -> Path:
    suffix = (os.getenv("BOT_LOG_SUFFIX") or "").strip()
    name = f"trades_{suffix}.tsv" if suffix else "trades.tsv"
    return Path(__file__).parent / name


LOG_PATH  = _log_path()
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
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_timestamp(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(normalized)
        except ValueError:
            return value
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(value)

# ── Public API ────────────────────────────────────────────────────────────────

def log_order(
    symbol: str,
    order_id: str,
    side: str,
    notional: float,
    *,
    alpaca_request: str,
    rationale: str,
    submitted_at=None,
):
    """Append a new order as 'pending'."""
    with _lock:
        rows = _read()
        rows.append({
            "symbol":       symbol,
            "order_id":     str(order_id),
            "side":         side,
            "notional":     f"{notional:.2f}",
            "status":       "pending",
            "alpaca_request": alpaca_request,
            "rationale":    rationale,
            "submitted_at": _format_timestamp(submitted_at) or _now(),
            "executed_at":  "",
            "filled_at":    "",
            "avg_price":    "",
        })
        _write(rows)

def update_order(
    order_id: str,
    status: str,
    avg_price: float = None,
    submitted_at=None,
    filled_at=None,
):
    """Update the status of an existing order. Marks fill time if status='filled'."""
    with _lock:
        rows = _read()
        for row in rows:
            if row["order_id"] == str(order_id):
                row["status"] = status
                if submitted_at is not None:
                    row["submitted_at"] = _format_timestamp(submitted_at) or row.get("submitted_at", "")
                if status == "filled":
                    executed_at = _format_timestamp(filled_at) or row.get("executed_at", "") or _now()
                    row["executed_at"] = executed_at
                    row["filled_at"] = executed_at
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
