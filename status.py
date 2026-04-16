"""
Legacy matplotlib status module compatibility shim.

The web dashboard is the primary UI now, but some tests and docs still expect
`status.py` to expose `parse_history()`.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).parent


def _bot_log_path() -> Path:
    return HERE / "bot.log"


def parse_history(symbol: str) -> tuple[list[str], list[float], list[float], float | None]:
    tag = symbol.replace("/", "")
    today = date.today()
    times: list[str] = []
    prices: list[float] = []
    floors: list[float] = []
    entry: float | None = None
    bot_log_path = _bot_log_path()
    if not bot_log_path.exists():
        return times, prices, floors, entry

    lines = bot_log_path.read_text().splitlines()
    last_start = 0
    for idx, line in enumerate(lines):
        if f"[{tag}]" in line and "BOT STARTED" in line:
            last_start = idx

    for line in lines[last_start:]:
        if f"[{tag}]" not in line:
            continue
        match = re.match(r"(\d{2}:\d{2}:\d{2})", line)
        if not match:
            continue
        dt = datetime.combine(today, datetime.strptime(match.group(1), "%H:%M:%S").time())
        if "Entry" in line and entry is None:
            price = re.search(r"\$([0-9,]+\.?\d*)", line)
            if price:
                entry = float(price.group(1).replace(",", ""))
            continue
        price_match = re.search(r"price=\$([0-9,]+\.?\d*)", line)
        floor_match = re.search(r"floor=\$([0-9,]+\.?\d*)", line)
        if price_match and floor_match:
            times.append(dt.strftime("%Y-%m-%dT%H:%M:%S"))
            prices.append(float(price_match.group(1).replace(",", "")))
            floors.append(float(floor_match.group(1).replace(",", "")))
    return times, prices, floors, entry


if __name__ == "__main__":
    print("status.py compatibility module restored. Use dashboard.py for the main UI.")
