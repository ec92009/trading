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

from sim import ABSORBER_SYMBOL, SYMBOLS, SimConfig, display, is_absorber, is_fractional, load_data

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


def _cfg_from_result(result: EvalResult) -> SimConfig:
    return SimConfig(
        initial=result.initial,
        base_tol=result.base_tol,
        stop_sell_pct=result.stop_sell_pct,
        trail_step=result.trail_step,
        trail_stop=result.trail_stop,
        stop_cooldown_days=result.stop_cooldown_days,
    )


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

    def buffer_price(i: int) -> float:
        return assets[ABSORBER_SYMBOL]["closes"][i]

    def total_value(i: int) -> float:
        buffer_value = buffer_qty[0] * buffer_price(i) if ABSORBER_SYMBOL in symbols else 0.0
        return round(cash + buffer_value + sum(st[sym]["qty"] * assets[sym]["closes"][i] for sym in symbols), 2)

    traded_today: set[str] = set()

    def can_trade(sym: str) -> bool:
        return sym not in traded_today

    def mark_traded(sym: str):
        traded_today.add(sym)

    def park_in_buffer(dollars: float, i: int):
        nonlocal cash
        if dollars <= 0:
            return
        if ABSORBER_SYMBOL in symbols and can_trade(ABSORBER_SYMBOL):
            qty, left = buy_qty(ABSORBER_SYMBOL, dollars, buffer_price(i))
            buffer_qty[0] += qty
            cash = round(cash + left, 2)
            if qty > 0:
                mark_traded(ABSORBER_SYMBOL)
            return
        cash = round(cash + dollars, 2)

    def raise_cash(required: float, i: int):
        nonlocal cash
        if required <= cash or ABSORBER_SYMBOL not in symbols or buffer_qty[0] <= 0 or not can_trade(ABSORBER_SYMBOL):
            return
        shortfall = required - cash
        btc_price = buffer_price(i)
        btc_sell_qty = min(buffer_qty[0], shortfall / btc_price)
        if btc_sell_qty <= 0:
            return
        proceeds = round(btc_sell_qty * btc_price, 2)
        buffer_qty[0] -= btc_sell_qty
        cash = round(cash + proceeds, 2)
        mark_traded(ABSORBER_SYMBOL)

    def refill_buffer_from_cash(i: int):
        nonlocal cash
        if cash <= 0 or ABSORBER_SYMBOL not in symbols or not can_trade(ABSORBER_SYMBOL):
            return
        qty, leftover = buy_qty(ABSORBER_SYMBOL, cash, buffer_price(i))
        buffer_qty[0] += qty
        cash = leftover
        if qty > 0:
            mark_traded(ABSORBER_SYMBOL)

    def rebalance_portfolio(i: int):
        nonlocal cash
        if not symbols:
            return
        target_value = total_value(i) / len(symbols)

        for sym in symbols:
            if not can_trade(sym):
                continue
            price = assets[sym]["closes"][i]
            current_value = st[sym]["qty"] * price
            excess = current_value - target_value
            if excess <= 0:
                continue
            if is_absorber(sym):
                sell_qty = excess / price
                if sell_qty <= 0:
                    continue
                st[sym]["qty"] -= sell_qty
                buffer_qty[0] += sell_qty
                mark_traded(sym)
                continue
            sell_qty = excess / price if is_fractional(sym) else math.floor(excess / price)
            if sell_qty <= 0:
                continue
            proceeds = round(sell_qty * price, 2)
            st[sym]["qty"] -= sell_qty
            mark_traded(sym)
            park_in_buffer(proceeds, i)

        deficits: list[tuple[float, str]] = []
        for sym in symbols:
            price = assets[sym]["closes"][i]
            gap = target_value - (st[sym]["qty"] * price)
            if gap > 0:
                deficits.append((gap, sym))
        deficits.sort(reverse=True)

        for _, sym in deficits:
            if not can_trade(sym):
                continue
            price = assets[sym]["closes"][i]
            current_value = st[sym]["qty"] * price
            gap = target_value - current_value
            if gap <= 0:
                continue
            if is_absorber(sym):
                needed_qty = gap / price
                moved_qty = min(buffer_qty[0], needed_qty)
                if moved_qty > 0:
                    buffer_qty[0] -= moved_qty
                    st[sym]["qty"] += moved_qty
                    mark_traded(sym)
                remaining_cost = round(max(0.0, gap - moved_qty * price), 2)
                if remaining_cost <= 0:
                    continue
                raise_cash(remaining_cost, i)
                spend = min(remaining_cost, cash)
                if spend <= 0:
                    continue
                st[sym]["qty"] += spend / price
                cash = round(cash - spend, 2)
                mark_traded(sym)
                continue
            if is_fractional(sym):
                required = gap
                raise_cash(required, i)
                spend = min(required, cash)
                if spend <= 0:
                    continue
                cash = round(cash - spend, 2)
                st[sym]["qty"] += spend / price
            else:
                target_shares = math.floor(target_value / price)
                current_shares = math.floor(st[sym]["qty"])
                needed = max(0, target_shares - current_shares)
                if needed <= 0:
                    continue
                required = needed * price
                raise_cash(required, i)
                affordable = min(needed, math.floor(cash / price))
                if affordable <= 0:
                    continue
                cash = round(cash - affordable * price, 2)
                st[sym]["qty"] += affordable
            mark_traded(sym)

        refill_buffer_from_cash(i)

    st: dict[str, dict[str, float]] = {}
    for sym in symbols:
        entry = assets[sym]["closes"][0]
        fp = floor_pct(sym, 0)
        tp = trigger_pct(sym, 0)
        qty, leftover = buy_qty(sym, per, entry)
        st[sym] = {
            "qty": qty,
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
        traded_today.clear()
        for sym in symbols:
            close = assets[sym]["closes"][i]
            low = assets[sym]["lows"][i]
            s = st[sym]

            if can_trade(sym) and i >= s["stop_ready_day"] and low <= s["floor"]:
                sp = s["floor"]
                sell_qty = s["qty"] * cfg.stop_sell_pct
                proceeds = round(sell_qty * sp, 2)
                s["qty"] -= sell_qty
                mark_traded(sym)
                fp = floor_pct(sym, i)
                tp = trigger_pct(sym, i)
                s["floor"] = round(sp * (1 - fp), 2)
                s["t_next"] = round(sp * (1 + tp), 2)
                s["stop_ready_day"] = i + int(cfg.stop_cooldown_days) + 1

                if is_absorber(sym):
                    buffer_qty[0] += sell_qty
                else:
                    park_in_buffer(proceeds, i)
                continue

            if close >= s["t_next"]:
                new_floor = round(close * cfg.trail_stop, 2)
                if new_floor > s["floor"]:
                    s["floor"] = new_floor
                    s["t_next"] = round(close * cfg.trail_step, 2)

        rebalance_portfolio(i)
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
    parser.add_argument("--train-start", type=str, help="Training window start date (YYYY-MM-DD)")
    parser.add_argument("--train-end", type=str, help="Training window end date, exclusive (YYYY-MM-DD)")
    parser.add_argument("--test-start", type=str, help="Holdout window start date (YYYY-MM-DD)")
    parser.add_argument("--test-end", type=str, help="Holdout window end date, exclusive (YYYY-MM-DD)")
    args = parser.parse_args()

    symbols = SYMBOLS[:]
    data = load_data(start=args.train_start, end=args.train_end) if (args.train_start or args.train_end) else load_data()

    coarse_params = _sample_params(args.samples, args.seed)
    coarse = sorted(_batched_map(data, symbols, args.initial, coarse_params, args.workers), key=lambda r: (r.score, r.final), reverse=True)

    refined_params = _refine_around(coarse[: args.refine_top], args.refine_samples, args.seed + 1)
    seen = set(coarse_params)
    refined_params = [p for p in refined_params if p not in seen]
    refined = sorted(_batched_map(data, symbols, args.initial, refined_params, args.workers), key=lambda r: (r.score, r.final), reverse=True)

    combined = sorted(itertools.chain(coarse, refined), key=lambda r: (r.score, r.final), reverse=True)
    best = combined[0]

    validation: dict | None = None
    if args.test_start or args.test_end:
        test_data = load_data(start=args.test_start, end=args.test_end)
        best_cfg = _cfg_from_result(best)
        opt_final, opt_return_pct, opt_max_dd_pct, opt_bh_final = _simulate_fast(best_cfg, test_data, symbols)
        default_cfg = SimConfig(initial=args.initial)
        def_final, def_return_pct, def_max_dd_pct, def_bh_final = _simulate_fast(default_cfg, test_data, symbols)
        validation = {
            "train_window": {
                "start": data["dates"][0],
                "end": data["dates"][-1],
                "days": len(data["dates"]),
            },
            "test_window": {
                "start": test_data["dates"][0],
                "end": test_data["dates"][-1],
                "days": len(test_data["dates"]),
            },
            "optimized_on_test": {
                "initial": args.initial,
                "final": opt_final,
                "return_pct": opt_return_pct,
                "max_dd_pct": opt_max_dd_pct,
                "bh_final": opt_bh_final,
                "bh_return_pct": round((opt_bh_final - args.initial) / args.initial * 100, 2),
            },
            "default_on_test": {
                "initial": args.initial,
                "final": def_final,
                "return_pct": def_return_pct,
                "max_dd_pct": def_max_dd_pct,
                "bh_final": def_bh_final,
                "bh_return_pct": round((def_bh_final - args.initial) / args.initial * 100, 2),
            },
        }

    payload = {
        "search": {
            "initial": args.initial,
            "symbols": [display(sym) for sym in symbols],
            "samples": args.samples,
            "refine_top": args.refine_top,
            "refine_samples": args.refine_samples,
            "workers": args.workers,
            "seed": args.seed,
            "train_start": args.train_start,
            "train_end": args.train_end,
            "test_start": args.test_start,
            "test_end": args.test_end,
        },
        "best": asdict(best),
        "top10": [asdict(r) for r in combined[:10]],
    }
    if validation is not None:
        payload["validation"] = validation
    args.json.write_text(json.dumps(payload, indent=2))

    print(_summarize("Top results", combined, limit=10))
    if validation is not None:
        print()
        print("Holdout validation")
        print(
            f"Optimized params on {validation['test_window']['start']} → {validation['test_window']['end']}: "
            f"final=${validation['optimized_on_test']['final']:,.2f} | "
            f"return={validation['optimized_on_test']['return_pct']:+.2f}% | "
            f"maxDD={validation['optimized_on_test']['max_dd_pct']:.2f}%"
        )
        print(
            f"Default params on {validation['test_window']['start']} → {validation['test_window']['end']}: "
            f"final=${validation['default_on_test']['final']:,.2f} | "
            f"return={validation['default_on_test']['return_pct']:+.2f}% | "
            f"maxDD={validation['default_on_test']['max_dd_pct']:.2f}%"
        )
        print(
            f"Buy-and-hold on holdout: final=${validation['optimized_on_test']['bh_final']:,.2f} | "
            f"return={validation['optimized_on_test']['bh_return_pct']:+.2f}%"
        )
    print()
    print(f"Saved detailed results to {args.json}")


if __name__ == "__main__":
    main()
