"""
Refit the hourly stop/trigger strategy on all available history for bot deployment.

- Train on all available history up to the latest holdout boundary
- No separate test window; this is for production parameter selection after research
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict
from pathlib import Path

from hourly_strategy import DEFAULT_SYMBOLS, DEFAULT_TARGET_WEIGHTS, HourlyConfig, load_hourly_data
from optimize_hourly_strategies import FRICTION, eval_cfg, sample_cfg

HERE = Path(__file__).parent
RESULT_PATH = HERE / "bot_refit_results.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Refit hourly strategy on all available history.")
    parser.add_argument("--initial", type=float, default=10_000.0)
    parser.add_argument("--samples", type=int, default=300)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--json", type=Path, default=RESULT_PATH)
    parser.add_argument("--train-start", default="2023-01-01")
    parser.add_argument("--train-end", default="2026-04-01")
    args = parser.parse_args()

    train = load_hourly_data(
        start=args.train_start,
        end=args.train_end,
        chosen_symbols=DEFAULT_SYMBOLS,
    )

    rng = random.Random(args.seed)
    candidates = [sample_cfg(args.initial, rng) for _ in range(args.samples)]
    ranked = sorted((eval_cfg(cfg, train) for cfg in candidates), key=lambda r: (r.score, r.final), reverse=True)
    best = ranked[0]
    best_cfg = HourlyConfig(
        initial=args.initial,
        base_tol=best.base_tol,
        stop_sell_pct=best.stop_sell_pct,
        trail_step=best.trail_step,
        trail_stop=best.trail_stop,
        stop_cooldown_days=best.stop_cooldown_days,
        rebalance_every_bars=best.rebalance_every_bars,
        enable_risk_controls=True,
        **FRICTION,
    )

    payload = {
        "search": {
            "symbols": DEFAULT_SYMBOLS,
            "target_weights": DEFAULT_TARGET_WEIGHTS,
            "initial": args.initial,
            "samples": args.samples,
            "seed": args.seed,
            "train_start": args.train_start,
            "train_end": args.train_end,
            "frequency": "hourly_stock_session",
            "friction": FRICTION,
        },
        "best_train": asdict(best),
        "recommended_bot_config": {
            "base_tol": best_cfg.base_tol,
            "stop_sell_pct": best_cfg.stop_sell_pct,
            "trail_step": best_cfg.trail_step,
            "trail_stop": best_cfg.trail_stop,
            "stop_cooldown_days": best_cfg.stop_cooldown_days,
            "target_weights": DEFAULT_TARGET_WEIGHTS,
        },
        "top10_train": [asdict(r) for r in ranked[:10]],
    }
    args.json.write_text(json.dumps(payload, indent=2))

    print("Hourly stop/trigger + rebalance full-history refit")
    print(
        f"Best train: final=${best.final:,.2f} | return={best.return_pct:+.2f}% | "
        f"maxDD={best.max_dd_pct:.2f}% | base_tol={best.base_tol:.4f} stop_sell={best.stop_sell_pct:.4f} "
        f"trail_step={best.trail_step:.4f} trail_stop={best.trail_stop:.4f} cooldown={best.stop_cooldown_days}d"
    )
    print(f"Saved detailed results to {args.json}")


if __name__ == "__main__":
    main()
