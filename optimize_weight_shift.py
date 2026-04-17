"""
Optimize the dynamic-weight sandbox on a train/test split.

Usage:
    .venv/bin/python optimize_weight_shift.py
    .venv/bin/python optimize_weight_shift.py --symbols TSLA,TSM,NVDA,PLTR,BTC-USD
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from sim import load_data
from weight_shift_strategy import WeightShiftConfig, simulate_weight_shift

HERE = Path(__file__).parent
RESULT_PATH = HERE / "weight_shift_optimizer_results.json"
DEFAULT_SYMBOLS = ["TSLA", "TSM", "NVDA", "PLTR", "BTC-USD"]


@dataclass(frozen=True)
class EvalResult:
    initial: float
    final: float
    return_pct: float
    max_dd_pct: float
    bh_final: float
    bh_return_pct: float
    score: float
    symbols: tuple[str, ...]
    down_shift_pct: float
    up_shift_pct: float


def _score(final_value: float, max_dd_pct: float) -> float:
    return round(final_value - (max_dd_pct * 3.0), 4)


def _parse_symbols(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _frange(step: float, upper: float) -> list[float]:
    values = []
    current = 0.0
    while current <= upper + 1e-9:
        values.append(round(current, 4))
        current += step
    return values


def _evaluate(
    data: dict,
    symbols: list[str],
    initial: float,
    down_shift_pct: float,
    up_shift_pct: float,
    *,
    fractional_stocks: bool,
) -> EvalResult:
    cfg = WeightShiftConfig(
        initial=initial,
        down_shift_pct=down_shift_pct,
        up_shift_pct=up_shift_pct,
        fractional_stocks=fractional_stocks,
    )
    result = simulate_weight_shift(cfg, data, chosen_symbols=symbols, record_events=False)
    return EvalResult(
        initial=initial,
        final=result["final"],
        return_pct=result["return_pct"],
        max_dd_pct=result["max_dd_pct"],
        bh_final=result["bh_final"],
        bh_return_pct=result["bh_return_pct"],
        score=_score(result["final"], result["max_dd_pct"]),
        symbols=tuple(result["symbols"]),
        down_shift_pct=down_shift_pct,
        up_shift_pct=up_shift_pct,
    )


def _summarize(results: list[EvalResult], limit: int = 10) -> str:
    lines = ["Top results"]
    for idx, result in enumerate(results[:limit], start=1):
        lines.append(
            f"{idx}. final=${result.final:,.2f} | return={result.return_pct:+.2f}% | maxDD={result.max_dd_pct:.2f}% | "
            f"X={result.down_shift_pct:.2%} Y={result.up_shift_pct:.2%}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize the dynamic-weight stop/trail strategy.")
    parser.add_argument("--initial", type=float, default=10_000.0)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--step", type=float, default=0.05, help="Grid spacing for X/Y, in decimal form.")
    parser.add_argument("--upper", type=float, default=0.50, help="Upper bound for X/Y, in decimal form.")
    parser.add_argument("--train-start", type=str, default="2023-01-01")
    parser.add_argument("--train-end", type=str, default="2024-01-01")
    parser.add_argument("--test-start", type=str, default="2024-01-01")
    parser.add_argument("--test-end", type=str, default="2026-04-01")
    parser.add_argument("--fractional-stocks", action="store_true", help="Allow fractional trading for all symbols.")
    parser.add_argument("--json", type=Path, default=RESULT_PATH)
    args = parser.parse_args()

    symbols = _parse_symbols(args.symbols)
    train = load_data(start=args.train_start, end=args.train_end)
    grid = _frange(args.step, args.upper)
    results = [
        _evaluate(
            train,
            symbols,
            args.initial,
            down_shift_pct,
            up_shift_pct,
            fractional_stocks=args.fractional_stocks,
        )
        for down_shift_pct, up_shift_pct in itertools.product(grid, grid)
    ]
    ranked = sorted(results, key=lambda item: (item.score, item.final), reverse=True)
    best = ranked[0]

    test = load_data(start=args.test_start, end=args.test_end)
    best_cfg = WeightShiftConfig(
        initial=args.initial,
        down_shift_pct=best.down_shift_pct,
        up_shift_pct=best.up_shift_pct,
        fractional_stocks=args.fractional_stocks,
    )
    best_test = simulate_weight_shift(best_cfg, test, chosen_symbols=symbols, record_events=False)
    baseline_test = simulate_weight_shift(
        WeightShiftConfig(
            initial=args.initial,
            down_shift_pct=0.0,
            up_shift_pct=0.0,
            fractional_stocks=args.fractional_stocks,
        ),
        test,
        chosen_symbols=symbols,
        record_events=False,
    )

    payload = {
        "search": {
            "initial": args.initial,
            "symbols": symbols,
            "step": args.step,
            "upper": args.upper,
            "train_start": args.train_start,
            "train_end": args.train_end,
            "test_start": args.test_start,
            "test_end": args.test_end,
            "fractional_stocks": args.fractional_stocks,
        },
        "best_train": asdict(best),
        "top10_train": [asdict(result) for result in ranked[:10]],
        "test": {
            "optimized": {
                "down_shift_pct": best.down_shift_pct,
                "up_shift_pct": best.up_shift_pct,
                "final": best_test["final"],
                "return_pct": best_test["return_pct"],
                "max_dd_pct": best_test["max_dd_pct"],
                "bh_final": best_test["bh_final"],
                "bh_return_pct": best_test["bh_return_pct"],
            },
            "baseline_xy_zero": {
                "final": baseline_test["final"],
                "return_pct": baseline_test["return_pct"],
                "max_dd_pct": baseline_test["max_dd_pct"],
                "bh_final": baseline_test["bh_final"],
                "bh_return_pct": baseline_test["bh_return_pct"],
            },
        },
    }
    args.json.write_text(json.dumps(payload, indent=2))

    print(_summarize(ranked))
    print()
    print(
        f"Holdout optimized ({best.down_shift_pct:.2%}, {best.up_shift_pct:.2%}) on "
        f"{test['dates'][0]} -> {test['dates'][-1]}: final=${best_test['final']:,.2f} | "
        f"return={best_test['return_pct']:+.2f}% | maxDD={best_test['max_dd_pct']:.2f}%"
    )
    print(
        f"Holdout baseline (X=0, Y=0): final=${baseline_test['final']:,.2f} | "
        f"return={baseline_test['return_pct']:+.2f}% | maxDD={baseline_test['max_dd_pct']:.2f}%"
    )
    print(
        f"Buy-and-hold on holdout: final=${best_test['bh_final']:,.2f} | "
        f"return={best_test['bh_return_pct']:+.2f}%"
    )
    print()
    print(f"Saved detailed results to {args.json}")


if __name__ == "__main__":
    main()
