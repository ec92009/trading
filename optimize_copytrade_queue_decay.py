"""
Grid search Capitol copy-trade queue size and daily point decay together.

Usage:
    ./.venv/bin/python optimize_copytrade_queue_decay.py
    ./.venv/bin/python optimize_copytrade_queue_decay.py --politician "Ro Khanna" --min-band "15K-50K"
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
    max_names: int
    daily_decay_pct: float
    train_return_pct: float
    train_final_equity: float
    train_spy_return_pct: float | None
    test_return_pct: float
    test_final_equity: float
    test_spy_return_pct: float | None
    full_return_pct: float
    full_final_equity: float
    full_spy_return_pct: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", default=str(SIGNALS_PATH))
    parser.add_argument("--politician", default="Ro Khanna")
    parser.add_argument("--min-band", default="50K-100K")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--entry-lag-trading-days", type=int, default=1)
    parser.add_argument("--train-start", default="2024-02-07")
    parser.add_argument("--train-end", default="2025-12-31")
    parser.add_argument("--test-start", default="2026-01-01")
    parser.add_argument("--test-end", default="2026-04-19")
    parser.add_argument("--queue-sizes", default="5,8,10,12,15,20")
    parser.add_argument("--decays", default="0,0.01,0.03,0.05,0.1,0.2,0.35,0.5,0.65,0.8,1.0")
    parser.add_argument("--top", type=int, default=12)
    return parser.parse_args()


def _parse_int_list(raw: str) -> list[int]:
    values = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if not values:
        raise ValueError("queue-sizes cannot be empty")
    return values


def _parse_float_list(raw: str) -> list[float]:
    values = [float(part.strip()) for part in raw.split(",") if part.strip()]
    if not values:
        raise ValueError("decays cannot be empty")
    return values


def _subset(signals: list[demo.DisclosureSignal], start: str, end: str) -> list[demo.DisclosureSignal]:
    return [signal for signal in signals if start <= signal.published_at <= end]


def _spy_return(result: dict) -> float | None:
    benchmark = result.get("benchmarks", {}).get("SPY_buy_and_hold")
    if benchmark is None:
        return None
    return benchmark["return_pct"]


def _matrix(results: list[SweepResult], *, field: str, queue_sizes: list[int], decays: list[float]) -> dict[str, dict[str, float]]:
    by_key = {(item.max_names, item.daily_decay_pct): item for item in results}
    payload: dict[str, dict[str, float]] = {}
    for queue_size in queue_sizes:
        row: dict[str, float] = {}
        for decay in decays:
            row[f"{decay:g}"] = getattr(by_key[(queue_size, decay)], field)
        payload[str(queue_size)] = row
    return payload


def main() -> None:
    args = parse_args()
    queue_sizes = sorted(set(_parse_int_list(args.queue_sizes)))
    decays = sorted(set(_parse_float_list(args.decays)))
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
    full_signals = _subset(signals, args.train_start, args.test_end)

    results: list[SweepResult] = []
    for queue_size in queue_sizes:
        for daily_decay_pct in decays:
            train_result = demo.simulate_with_market(
                train_signals,
                market=market,
                trading_days=trading_days,
                capital=args.capital,
                min_band=args.min_band,
                max_names=queue_size,
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
                max_names=queue_size,
                entry_lag_trading_days=args.entry_lag_trading_days,
                daily_decay_pct=daily_decay_pct,
                end=args.test_end,
                skipped_symbols=skipped_symbols,
            )
            full_result = demo.simulate_with_market(
                full_signals,
                market=market,
                trading_days=trading_days,
                capital=args.capital,
                min_band=args.min_band,
                max_names=queue_size,
                entry_lag_trading_days=args.entry_lag_trading_days,
                daily_decay_pct=daily_decay_pct,
                end=args.test_end,
                skipped_symbols=skipped_symbols,
            )
            results.append(
                SweepResult(
                    max_names=queue_size,
                    daily_decay_pct=daily_decay_pct,
                    train_return_pct=train_result["return_pct"],
                    train_final_equity=train_result["final_equity"],
                    train_spy_return_pct=_spy_return(train_result),
                    test_return_pct=test_result["return_pct"],
                    test_final_equity=test_result["final_equity"],
                    test_spy_return_pct=_spy_return(test_result),
                    full_return_pct=full_result["return_pct"],
                    full_final_equity=full_result["final_equity"],
                    full_spy_return_pct=_spy_return(full_result),
                )
            )

    results.sort(
        key=lambda item: (
            item.train_final_equity,
            item.test_final_equity,
            item.full_final_equity,
            -item.daily_decay_pct,
            -item.max_names,
        ),
        reverse=True,
    )

    payload = {
        "politician": args.politician,
        "min_band": args.min_band,
        "train_window": {"start": args.train_start, "end": args.train_end},
        "test_window": {"start": args.test_start, "end": args.test_end},
        "search": {"queue_sizes": queue_sizes, "decays": decays, "samples": len(results)},
        "best": asdict(results[0]),
        "top_results": [asdict(result) for result in results[: args.top]],
        "test_return_matrix": _matrix(results, field="test_return_pct", queue_sizes=queue_sizes, decays=decays),
        "full_return_matrix": _matrix(results, field="full_return_pct", queue_sizes=queue_sizes, decays=decays),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
