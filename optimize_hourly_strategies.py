"""
Train/evaluate hourly rebalance variants on a shared Alpaca hourly dataset.

- Train on 2023
- Hold out January 2, 2024 through March 31, 2026
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from hourly_strategy import DEFAULT_SYMBOLS, HourlyConfig, load_hourly_data, simulate_hourly

HERE = Path(__file__).parent
RESULT_PATH = HERE / "hourly_strategy_results.json"
FRICTION = {
    "stock_slippage_bps": 5.0,
    "crypto_slippage_bps": 10.0,
    "crypto_taker_fee_bps": 25.0,
    "equity_sec_sell_fee_rate": 0.00002060,
    "equity_taf_per_share": 0.000195,
    "equity_taf_max_per_trade": 9.79,
    "equity_cat_per_share": 0.000046,
}


@dataclass(frozen=True)
class HourlyEval:
    initial: float
    final: float
    return_pct: float
    max_dd_pct: float
    bh_final: float
    bh_return_pct: float
    turnover: float
    n_stops: int
    n_trails: int
    n_rebalances: int
    score: float
    base_tol: float
    stop_sell_pct: float
    trail_step: float
    trail_stop: float
    stop_cooldown_days: int
    rebalance_every_bars: int


def _score(summary: dict) -> float:
    return summary["final"] - 10 * summary["max_dd_pct"] - 0.001 * summary["turnover"]


def eval_cfg(cfg: HourlyConfig, data: dict) -> HourlyEval:
    summary = simulate_hourly(cfg, data, record_events=False)["summary"]
    return HourlyEval(
        initial=cfg.initial,
        final=summary["final"],
        return_pct=summary["return_pct"],
        max_dd_pct=summary["max_dd_pct"],
        bh_final=summary["bh_final"],
        bh_return_pct=summary["bh_return_pct"],
        turnover=summary["turnover"],
        n_stops=summary["n_stops"],
        n_trails=summary["n_trails"],
        n_rebalances=summary["n_rebalances"],
        score=_score(summary),
        base_tol=cfg.base_tol,
        stop_sell_pct=cfg.stop_sell_pct,
        trail_step=cfg.trail_step,
        trail_stop=cfg.trail_stop,
        stop_cooldown_days=cfg.stop_cooldown_days,
        rebalance_every_bars=cfg.rebalance_every_bars,
    )


def sample_cfg(initial: float, rng: random.Random) -> HourlyConfig:
    return HourlyConfig(
        initial=initial,
        base_tol=round(rng.uniform(0.003, 0.04), 4),
        stop_sell_pct=round(rng.uniform(0.10, 0.90), 4),
        trail_step=round(rng.uniform(1.005, 1.06), 4),
        trail_stop=round(rng.uniform(0.94, 0.999), 4),
        stop_cooldown_days=rng.randint(0, 5),
        rebalance_every_bars=1,
        enable_risk_controls=True,
        **FRICTION,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize hourly rebalance strategies.")
    parser.add_argument("--initial", type=float, default=10_000.0)
    parser.add_argument("--samples", type=int, default=300)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--json", type=Path, default=RESULT_PATH)
    args = parser.parse_args()

    train = load_hourly_data(
        start="2023-01-01",
        end="2024-01-01",
        chosen_symbols=DEFAULT_SYMBOLS,
    )
    test = load_hourly_data(
        start="2024-01-02",
        end="2026-04-01",
        chosen_symbols=DEFAULT_SYMBOLS,
    )

    rebalance_only_cfg = HourlyConfig(
        initial=args.initial,
        rebalance_every_bars=1,
        enable_risk_controls=False,
        **FRICTION,
    )
    rebalance_only_train = eval_cfg(rebalance_only_cfg, train)
    rebalance_only_test = eval_cfg(rebalance_only_cfg, test)

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
    best_test = eval_cfg(best_cfg, test)

    payload = {
        "search": {
            "symbols": DEFAULT_SYMBOLS,
            "initial": args.initial,
            "samples": args.samples,
            "seed": args.seed,
            "train_start": "2023-01-01",
            "train_end": "2024-01-01",
            "test_start": "2024-01-02",
            "test_end": "2026-04-01",
            "frequency": "hourly_stock_session",
            "friction": FRICTION,
        },
        "rebalance_only": {
            "train": asdict(rebalance_only_train),
            "test": asdict(rebalance_only_test),
        },
        "stop_trigger_rebalance": {
            "best_train": asdict(best),
            "best_test": asdict(best_test),
            "top10_train": [asdict(r) for r in ranked[:10]],
        },
    }
    args.json.write_text(json.dumps(payload, indent=2))

    print("Hourly rebalance only")
    print(
        f"Train: final=${rebalance_only_train.final:,.2f} | return={rebalance_only_train.return_pct:+.2f}% | "
        f"maxDD={rebalance_only_train.max_dd_pct:.2f}% | turnover=${rebalance_only_train.turnover:,.2f}"
    )
    print(
        f"Test : final=${rebalance_only_test.final:,.2f} | return={rebalance_only_test.return_pct:+.2f}% | "
        f"maxDD={rebalance_only_test.max_dd_pct:.2f}% | turnover=${rebalance_only_test.turnover:,.2f}"
    )
    print()
    print("Hourly stop/trigger + rebalance")
    print(
        f"Train best: final=${best.final:,.2f} | return={best.return_pct:+.2f}% | "
        f"maxDD={best.max_dd_pct:.2f}% | base_tol={best.base_tol:.4f} stop_sell={best.stop_sell_pct:.4f} "
        f"trail_step={best.trail_step:.4f} trail_stop={best.trail_stop:.4f} cooldown={best.stop_cooldown_days}d"
    )
    print(
        f"Test best : final=${best_test.final:,.2f} | return={best_test.return_pct:+.2f}% | "
        f"maxDD={best_test.max_dd_pct:.2f}% | turnover=${best_test.turnover:,.2f}"
    )
    print()
    print(f"Saved detailed results to {args.json}")


if __name__ == "__main__":
    main()
