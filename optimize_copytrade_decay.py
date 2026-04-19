"""
Grid search the Capitol copy-trade daily point-decay parameter.

Usage:
    ./.venv/bin/python optimize_copytrade_decay.py
    ./.venv/bin/python optimize_copytrade_decay.py --politician "Markwayne Mullin" --min-band "15K-50K"
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import copytrade_demo as demo

HERE = Path(__file__).parent
SIGNALS_PATH = HERE / "copytrade_signals.json"


@dataclass(frozen=True)
class SweepResult:
    daily_decay_pct: float
    train_return_pct: float
    train_final_equity: float
    train_spy_return_pct: float | None
    test_return_pct: float
    test_final_equity: float
    test_spy_return_pct: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", default=str(SIGNALS_PATH))
    parser.add_argument("--politician", default="Markwayne Mullin")
    parser.add_argument("--min-band", default="50K-100K")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--max-names", type=int, default=demo.DEFAULT_MAX_NAMES)
    parser.add_argument("--entry-lag-trading-days", type=int, default=1)
    parser.add_argument("--train-start", default="2025-08-13")
    parser.add_argument("--train-end", default="2025-12-31")
    parser.add_argument("--test-start", default="2026-01-01")
    parser.add_argument("--test-end", default="2026-04-19")
    parser.add_argument("--lower", type=float, default=0.0)
    parser.add_argument("--upper", type=float, default=0.05)
    parser.add_argument("--step", type=float, default=0.0005)
    parser.add_argument("--top", type=int, default=10)
    return parser.parse_args()


def _frange(lower: float, upper: float, step: float) -> list[float]:
    if step <= 0:
        raise ValueError("step must be > 0")
    values: list[float] = []
    current = lower
    while current <= upper + 1e-12:
        values.append(round(current, 6))
        current += step
    return values


def _subset(signals: list[demo.DisclosureSignal], start: str, end: str) -> list[demo.DisclosureSignal]:
    return [signal for signal in signals if start <= signal.published_at <= end]


def _spy_return(result: dict) -> float | None:
    benchmark = result.get("benchmarks", {}).get("SPY_buy_and_hold")
    if benchmark is None:
        return None
    return benchmark["return_pct"]


def main() -> None:
    args = parse_args()
    signals = demo.load_signals(Path(args.signals), politician=args.politician)

    combined = _subset(signals, args.train_start, args.test_end)
    eligible = [signal for signal in combined if demo.qualifies(signal, args.min_band) and demo.target_points(signal) > 0]
    if not eligible:
        raise SystemExit("No eligible signals for this search configuration.")

    market_start = min(signal.published_at for signal in eligible)
    symbols = sorted({signal.symbol for signal in eligible})
    trading_days, market, skipped_symbols = demo.load_market_series(symbols, start=market_start, end=args.test_end)

    train_signals = _subset(signals, args.train_start, args.train_end)
    test_signals = _subset(signals, args.test_start, args.test_end)

    results: list[SweepResult] = []
    for daily_decay_pct in _frange(args.lower, args.upper, args.step):
        train_result = demo.simulate_with_market(
            train_signals,
            market=market,
            trading_days=trading_days,
            capital=args.capital,
            min_band=args.min_band,
            max_names=args.max_names,
            entry_lag_trading_days=args.entry_lag_trading_days,
            daily_decay_pct=daily_decay_pct,
            end=args.train_end,
            skipped_symbols=skipped_symbols,
        )
        test_result = demo.simulate_with_market(
            test_signals,
            market=market,
            trading_days=trading_days,
            capital=args.capital,
            min_band=args.min_band,
            max_names=args.max_names,
            entry_lag_trading_days=args.entry_lag_trading_days,
            daily_decay_pct=daily_decay_pct,
            end=args.test_end,
            skipped_symbols=skipped_symbols,
        )
        results.append(
            SweepResult(
                daily_decay_pct=daily_decay_pct,
                train_return_pct=train_result["return_pct"],
                train_final_equity=train_result["final_equity"],
                train_spy_return_pct=_spy_return(train_result),
                test_return_pct=test_result["return_pct"],
                test_final_equity=test_result["final_equity"],
                test_spy_return_pct=_spy_return(test_result),
            )
        )

    results.sort(
        key=lambda item: (
            item.train_final_equity,
            item.test_final_equity,
            -item.daily_decay_pct,
        ),
        reverse=True,
    )

    payload = {
        "politician": args.politician,
        "min_band": args.min_band,
        "train_window": {"start": args.train_start, "end": args.train_end},
        "test_window": {"start": args.test_start, "end": args.test_end},
        "search": {"lower": args.lower, "upper": args.upper, "step": args.step, "samples": len(results)},
        "best": asdict(results[0]),
        "top_results": [asdict(result) for result in results[: args.top]],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
