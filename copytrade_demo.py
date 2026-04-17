"""
Weight-based CapitolTrades copy-trade demo.

This is a focused sandbox for the "copy only large disclosed trades" idea:

- uses CapitolTrades publication dates as signal timestamps
- filters to stock trades at or above a chosen size band
- converts each qualifying band into a target portfolio weight
- rebalances only on publication days

Usage:
    python3 copytrade_demo.py
    python3 copytrade_demo.py --capital 25000 --min-band 100K-250K
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import yfinance as yf

HERE = Path(__file__).parent
SIGNALS_PATH = HERE / "copytrade_signals.json"

BAND_ORDER = ["1K-15K", "15K-50K", "50K-100K", "100K-250K", "250K-500K", "500K-1M", "1M-5M", "5M+"]
BAND_RANK = {band: idx for idx, band in enumerate(BAND_ORDER)}
BAND_WEIGHTS = {
    "50K-100K": 0.02,
    "100K-250K": 0.04,
    "250K-500K": 0.06,
    "500K-1M": 0.08,
    "1M-5M": 0.10,
    "5M+": 0.12,
}


@dataclass(frozen=True)
class DisclosureSignal:
    published_at: str
    traded_at: str
    politician: str
    symbol: str
    side: str
    size_band: str
    source: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", default=str(SIGNALS_PATH))
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--min-band", default="50K-100K")
    return parser.parse_args()


def load_signals(path: Path) -> list[DisclosureSignal]:
    raw = json.loads(path.read_text())
    return [DisclosureSignal(**item) for item in raw]


def qualifies(signal: DisclosureSignal, min_band: str) -> bool:
    if signal.size_band not in BAND_RANK or min_band not in BAND_RANK:
        return False
    return BAND_RANK[signal.size_band] >= BAND_RANK[min_band]


def target_weight(signal: DisclosureSignal) -> float:
    return BAND_WEIGHTS.get(signal.size_band, 0.0)


def download_prices(symbols: list[str], start: str, end: str) -> dict[str, dict]:
    data: dict[str, dict] = {}
    for symbol in symbols:
        df = yf.Ticker(symbol).history(start=start, end=end)[["Close"]].dropna()
        df.index = [idx.date().isoformat() for idx in df.index]
        data[symbol] = {day: float(row.Close) for day, row in df.iterrows()}
    return data


def first_price_on_or_after(series: dict[str, float], day: str) -> tuple[str, float] | None:
    for price_day in sorted(series):
        if price_day >= day:
            return price_day, series[price_day]
    return None


def simulate(signals: list[DisclosureSignal], capital: float, min_band: str) -> dict:
    eligible = [sig for sig in signals if qualifies(sig, min_band) and target_weight(sig) > 0]
    if not eligible:
        return {"capital": capital, "events": [], "final_equity": capital, "positions": {}}

    eligible.sort(key=lambda sig: (sig.published_at, sig.symbol, sig.side))
    symbols = sorted({sig.symbol for sig in eligible})
    start = min(sig.published_at for sig in eligible)
    end = "2026-04-18"
    prices = download_prices(symbols, start, end)

    events: list[dict] = []
    cash = capital
    positions: dict[str, float] = defaultdict(float)
    weights: dict[str, float] = defaultdict(float)

    def equity(as_of: str) -> float:
        total = cash
        for symbol, qty in positions.items():
            quote = first_price_on_or_after(prices[symbol], as_of)
            if quote:
                total += qty * quote[1]
        return round(total, 2)

    grouped: dict[str, list[DisclosureSignal]] = defaultdict(list)
    for sig in eligible:
        grouped[sig.published_at].append(sig)

    for published_at in sorted(grouped):
        today_equity = equity(published_at)
        for sig in grouped[published_at]:
            if sig.side == "buy":
                weights[sig.symbol] = target_weight(sig)
            elif sig.side == "sell":
                weights[sig.symbol] = 0.0

        for symbol in sorted({sig.symbol for sig in grouped[published_at]}):
            symbol_signals = [sig for sig in grouped[published_at] if sig.symbol == symbol]
            if all(sig.side == "sell" for sig in symbol_signals) and positions[symbol] <= 0:
                events.append(
                    {
                        "published_at": published_at,
                        "symbol": symbol,
                        "action": "no-op",
                        "reason": "sell signal with no copied position",
                    }
                )
                continue
            quote = first_price_on_or_after(prices[symbol], published_at)
            if not quote:
                events.append(
                    {
                        "published_at": published_at,
                        "symbol": symbol,
                        "action": "skip",
                        "reason": "no market data",
                    }
                )
                continue
            fill_day, price = quote
            current_value = positions[symbol] * price
            desired_value = today_equity * weights[symbol]
            delta = round(desired_value - current_value, 2)
            if abs(delta) < 1.0:
                continue
            if delta > 0:
                spend = min(delta, cash)
                if spend < 1.0:
                    continue
                qty = spend / price
                positions[symbol] += qty
                cash = round(cash - spend, 2)
                action = "buy"
                amount = spend
            else:
                sell_value = min(current_value, abs(delta))
                if sell_value < 1.0:
                    continue
                qty = sell_value / price
                positions[symbol] = max(0.0, positions[symbol] - qty)
                cash = round(cash + sell_value, 2)
                action = "sell"
                amount = sell_value

            events.append(
                {
                    "published_at": published_at,
                    "fill_day": fill_day,
                    "symbol": symbol,
                    "action": action,
                    "price": round(price, 2),
                    "amount": round(amount, 2),
                    "target_weight": round(weights[symbol], 4),
                }
            )

    last_day = max(max(series) for series in prices.values())
    final_equity = equity(last_day)
    open_positions = {}
    for symbol, qty in positions.items():
        if qty <= 0:
            continue
        price = first_price_on_or_after(prices[symbol], last_day)
        if not price:
            continue
        open_positions[symbol] = {
            "qty": round(qty, 6),
            "price": round(price[1], 2),
            "value": round(qty * price[1], 2),
            "weight": round((qty * price[1]) / final_equity, 4) if final_equity else 0.0,
        }

    return {
        "capital": capital,
        "min_band": min_band,
        "signals_used": len(eligible),
        "events": events,
        "final_equity": final_equity,
        "return_pct": round((final_equity / capital - 1) * 100, 2),
        "cash": round(cash, 2),
        "positions": open_positions,
    }


def main():
    args = parse_args()
    signals = load_signals(Path(args.signals))
    result = simulate(signals, capital=args.capital, min_band=args.min_band)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
