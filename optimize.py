"""
Hyperparameter search for the sandbox portfolio simulation.

Usage:
    .venv/bin/python optimize.py
    .venv/bin/python optimize.py --workers 8 --samples 5000
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from sim import ABSORBER_SYMBOL, SYMBOLS, SimConfig, display, is_fractional, load_data

HERE = Path(__file__).parent
RESULT_PATH = HERE / "optimizer_results.json"


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
    base_tol: float
    stop_sell_pct: float
    trail_step: float
    trail_stop: float
    stop_cooldown_days: int


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _simulate_fast(cfg: SimConfig, data: dict, symbols: list[str]) -> tuple[float, float, float, float]:
    dates = data["dates"]
    assets = data["assets"]
    betas = data["betas"]
    n = len(dates)
    per = cfg.initial / len(symbols)
    cash = 0.0
    buffer_qty = [0.0]

    def floor_pct(sym: str, i: int) -> float:
        return max(0.005, cfg.base_tol * betas[sym][i])

    def trigger_pct(sym: str, i: int) -> float:
        return max(0.005, cfg.base_tol * betas[sym][i])

    def buy_qty(sym: str, dollars: float, price: float) -> tuple[float, float]:
        if dollars <= 0:
            return 0.0, 0.0
        if is_fractional(sym):
            qty = dollars / price
            spent = dollars
        else:
            qty = math.floor(dollars / price)
            spent = qty * price
        return qty, round(dollars - spent, 2)

    def buy_absorber(dollars: float, i: int):
        nonlocal cash
        if dollars <= 0:
            return
        if ABSORBER_SYMBOL in symbols:
            qty, left = buy_qty(ABSORBER_SYMBOL, dollars, assets[ABSORBER_SYMBOL]["closes"][i])
            buffer_qty[0] += qty
            cash = round(cash + left, 2)
            return
        cash = round(cash + dollars, 2)

    def reconcile_targets(i: int):
        nonlocal cash
        if ABSORBER_SYMBOL in symbols:
            btc_price = assets[ABSORBER_SYMBOL]["closes"][i]
        for sym in symbols:
            if is_fractional(sym):
                continue
            target_whole = math.floor(st[sym]["target_qty"])
            current_whole = math.floor(st[sym]["qty"])
            needed = max(0, target_whole - current_whole)
            if needed <= 0:
                continue
            price = assets[sym]["closes"][i]
            required = needed * price
            if cash < required and ABSORBER_SYMBOL in symbols and buffer_qty[0] > 0:
                shortfall = required - cash
                btc_sell_qty = min(buffer_qty[0], shortfall / btc_price)
                if btc_sell_qty > 0:
                    proceeds = round(btc_sell_qty * btc_price, 2)
                    buffer_qty[0] -= btc_sell_qty
                    cash = round(cash + proceeds, 2)
            affordable = min(needed, math.floor(cash / price))
            if affordable <= 0:
                continue
            cash = round(cash - affordable * price, 2)
            st[sym]["qty"] += affordable

    st: dict[str, dict[str, float]] = {}
    for sym in symbols:
        entry = assets[sym]["closes"][0]
        fp = floor_pct(sym, 0)
        tp = trigger_pct(sym, 0)
        qty, leftover = buy_qty(sym, per, entry)
        st[sym] = {
            "qty": qty,
            "target_qty": (per / entry) if not is_fractional(sym) else qty,
            "floor": round(entry * (1 - fp), 2),
            "t_next": round(entry * (1 + tp), 2),
            "stop_ready_day": 1,
        }
        cash = round(cash + leftover, 2)

    if cash > 0 and ABSORBER_SYMBOL in symbols:
        qty, leftover = buy_qty(ABSORBER_SYMBOL, cash, assets[ABSORBER_SYMBOL]["closes"][0])
        buffer_qty[0] += qty
        cash = leftover

    total0 = cfg.initial
    peak = total0
    max_dd = 0.0

    for i in range(1, n):
        for sym in symbols:
            close = assets[sym]["closes"][i]
            low = assets[sym]["lows"][i]
            s = st[sym]

            if i >= s["stop_ready_day"] and low <= s["floor"]:
                sp = s["floor"]
                sell_qty = s["qty"] * cfg.stop_sell_pct
                proceeds = round(sell_qty * sp, 2)
                s["qty"] -= sell_qty
                fp = floor_pct(sym, i)
                tp = trigger_pct(sym, i)
                s["floor"] = round(sp * (1 - fp), 2)
                s["t_next"] = round(sp * (1 + tp), 2)
                s["stop_ready_day"] = i + int(cfg.stop_cooldown_days) + 1

                others = [other for other in symbols if other != sym]
                if not others:
                    cash = round(cash + proceeds, 2)
                else:
                    vals = {other: st[other]["qty"] * assets[other]["closes"][i] for other in others}
                    total = sum(vals.values())
                    for other in others:
                        share = proceeds * (vals[other] / total if total > 0 else 1 / len(others))
                        if not is_fractional(other):
                            st[other]["target_qty"] += share / assets[other]["closes"][i]
                    buy_absorber(proceeds, i)
                    reconcile_targets(i)
                continue

            if close >= s["t_next"]:
                new_floor = round(close * cfg.trail_stop, 2)
                if new_floor > s["floor"]:
                    s["floor"] = new_floor
                    s["t_next"] = round(close * cfg.trail_step, 2)

        buffer_value = buffer_qty[0] * assets[ABSORBER_SYMBOL]["closes"][i] if ABSORBER_SYMBOL in symbols else 0.0
        total = cash + buffer_value + sum(st[sym]["qty"] * assets[sym]["closes"][i] for sym in symbols)
        peak = max(peak, total)
        if peak > 0:
            max_dd = max(max_dd, (peak - total) / peak)

    buffer_final = buffer_qty[0] * assets[ABSORBER_SYMBOL]["closes"][-1] if ABSORBER_SYMBOL in symbols else 0.0
    final = round(cash + buffer_final + sum(st[sym]["qty"] * assets[sym]["closes"][-1] for sym in symbols), 2)
    init_qtys = {sym: per / assets[sym]["closes"][0] for sym in symbols}
    bh_final = round(sum(init_qtys[sym] * assets[sym]["closes"][-1] for sym in symbols), 2)
    return final, round((final - cfg.initial) / cfg.initial * 100, 2), round(max_dd * 100, 2), bh_final


def _score(final_value: float, max_dd_pct: float) -> float:
    # Primary target is ending value, with a light drawdown penalty to break close ties.
    return round(final_value - (max_dd_pct * 3.0), 4)


def _evaluate_one(args: tuple[dict, list[str], float, tuple[float, float, float, float, int]]) -> EvalResult:
    data, symbols, initial, params = args
    base_tol, stop_sell_pct, trail_step, trail_stop, stop_cooldown_days = params
    cfg = SimConfig(
        initial=initial,
        base_tol=base_tol,
        stop_sell_pct=stop_sell_pct,
        trail_step=trail_step,
        trail_stop=trail_stop,
        stop_cooldown_days=stop_cooldown_days,
    )
    final, return_pct, max_dd_pct, bh_final = _simulate_fast(cfg, data, symbols)
    bh_return_pct = round((bh_final - initial) / initial * 100, 2)
    return EvalResult(
        initial=initial,
        final=final,
        return_pct=return_pct,
        max_dd_pct=max_dd_pct,
        bh_final=bh_final,
        bh_return_pct=bh_return_pct,
        score=_score(final, max_dd_pct),
        symbols=tuple(display(sym) for sym in symbols),
        base_tol=base_tol,
        stop_sell_pct=stop_sell_pct,
        trail_step=trail_step,
        trail_stop=trail_stop,
        stop_cooldown_days=stop_cooldown_days,
    )


def _batched_map(data: dict, symbols: list[str], initial: float, params: list[tuple[float, float, float, float, int]], workers: int) -> list[EvalResult]:
    jobs = [(data, symbols, initial, p) for p in params]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_evaluate_one, jobs, chunksize=max(1, len(jobs) // (workers * 8) or 1)))


def _sample_params(samples: int, seed: int) -> list[tuple[float, float, float, float, int]]:
    rng = random.Random(seed)
    params = set()
    while len(params) < samples:
        params.add((
            round(rng.uniform(0.01, 0.15), 4),
            round(rng.uniform(0.01, 0.50), 4),
            round(rng.uniform(1.01, 1.25), 4),
            round(rng.uniform(0.80, 0.99), 4),
            rng.randint(0, 20),
        ))
    return list(params)


def _refine_around(top: list[EvalResult], per_result: int, seed: int) -> list[tuple[float, float, float, float, int]]:
    rng = random.Random(seed)
    params = set()
    for result in top:
        for _ in range(per_result):
            params.add((
                round(_clip(rng.gauss(result.base_tol, 0.012), 0.005, 0.20), 4),
                round(_clip(rng.gauss(result.stop_sell_pct, 0.05), 0.005, 0.75), 4),
                round(_clip(rng.gauss(result.trail_step, 0.02), 1.005, 1.35), 4),
                round(_clip(rng.gauss(result.trail_stop, 0.025), 0.70, 0.995), 4),
                max(0, min(20, int(round(rng.gauss(result.stop_cooldown_days, 2.0))))),
            ))
    return list(params)


def _summarize(title: str, results: list[EvalResult], limit: int = 5) -> str:
    lines = [title]
    for idx, result in enumerate(results[:limit], start=1):
        lines.append(
            f"{idx}. final=${result.final:,.2f} | return={result.return_pct:+.2f}% | maxDD={result.max_dd_pct:.2f}% | "
            f"base_tol={result.base_tol:.4f} stop_sell={result.stop_sell_pct:.4f} "
            f"trail_step={result.trail_step:.4f} trail_stop={result.trail_stop:.4f} "
            f"cooldown={result.stop_cooldown_days}d"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize sandbox parameters on the current 1-year window.")
    parser.add_argument("--initial", type=float, default=10_000.0)
    parser.add_argument("--samples", type=int, default=4000, help="Random coarse samples")
    parser.add_argument("--refine-top", type=int, default=24, help="Top coarse results to refine around")
    parser.add_argument("--refine-samples", type=int, default=150, help="Refinement samples per top result")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--json", type=Path, default=RESULT_PATH)
    args = parser.parse_args()

    symbols = SYMBOLS[:]
    data = load_data()

    coarse_params = _sample_params(args.samples, args.seed)
    coarse = sorted(_batched_map(data, symbols, args.initial, coarse_params, args.workers), key=lambda r: (r.score, r.final), reverse=True)

    refined_params = _refine_around(coarse[: args.refine_top], args.refine_samples, args.seed + 1)
    seen = set(coarse_params)
    refined_params = [p for p in refined_params if p not in seen]
    refined = sorted(_batched_map(data, symbols, args.initial, refined_params, args.workers), key=lambda r: (r.score, r.final), reverse=True)

    combined = sorted(itertools.chain(coarse, refined), key=lambda r: (r.score, r.final), reverse=True)
    best = combined[0]

    payload = {
        "search": {
            "initial": args.initial,
            "symbols": [display(sym) for sym in symbols],
            "samples": args.samples,
            "refine_top": args.refine_top,
            "refine_samples": args.refine_samples,
            "workers": args.workers,
            "seed": args.seed,
        },
        "best": asdict(best),
        "top10": [asdict(r) for r in combined[:10]],
    }
    args.json.write_text(json.dumps(payload, indent=2))

    print(_summarize("Top results", combined, limit=10))
    print()
    print(f"Saved detailed results to {args.json}")


if __name__ == "__main__":
    main()
