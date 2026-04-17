"""
Dynamic-weight sandbox strategy.

Idea:
- Start from equal weights across a chosen basket.
- If an asset hits its stop floor, reduce its target weight by X% of its
  current target weight and redistribute that weight equally to the others.
- If an asset clears its upper trail trigger, increase its target weight by Y%
  of its current target weight, funded equally by the other assets.
- Do not trade intraday for those events; rebalance once at the close.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sim import FRACTIONAL_SYMBOLS, SYMBOLS, display


@dataclass
class WeightShiftConfig:
    initial: float = 10_000.0
    base_tol: float = 0.05
    trail_step: float = 1.05
    trail_stop: float = 0.95
    down_shift_pct: float = 0.25
    up_shift_pct: float = 0.10
    fractional_stocks: bool = False


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        even = 1.0 / len(weights)
        return {sym: even for sym in weights}
    return {sym: val / total for sym, val in weights.items()}


def _is_fractional(sym: str, cfg: WeightShiftConfig) -> bool:
    return cfg.fractional_stocks or sym in FRACTIONAL_SYMBOLS


def _buy_qty(sym: str, dollars: float, price: float, cfg: WeightShiftConfig) -> tuple[float, float]:
    if dollars <= 0:
        return 0.0, 0.0
    if _is_fractional(sym, cfg):
        return dollars / price, 0.0
    qty = math.floor(dollars / price)
    spent = qty * price
    return qty, round(dollars - spent, 2)


def _shift_down(weights: dict[str, float], sym: str, pct: float) -> tuple[float, float]:
    current = weights[sym]
    delta = current * pct
    others = [name for name in weights if name != sym]
    if delta <= 0 or not others:
        return current, current
    weights[sym] = current - delta
    share = delta / len(others)
    for other in others:
        weights[other] += share
    weights.update(_normalize(weights))
    return current, weights[sym]


def _shift_up(weights: dict[str, float], sym: str, pct: float) -> tuple[float, float]:
    current = weights[sym]
    requested = current * pct
    others = [name for name in weights if name != sym]
    if requested <= 0 or not others:
        return current, current

    max_equal_take = min(weights[other] for other in others) * len(others)
    delta = min(requested, max_equal_take)
    if delta <= 0:
        return current, current

    share = delta / len(others)
    for other in others:
        weights[other] = max(0.0, weights[other] - share)
    weights[sym] += delta
    weights.update(_normalize(weights))
    return current, weights[sym]


def simulate_weight_shift(
    cfg: WeightShiftConfig,
    data: dict,
    chosen_symbols: list[str] | None = None,
    *,
    record_events: bool = True,
) -> dict:
    chosen_symbols = chosen_symbols[:] if chosen_symbols else SYMBOLS[:]
    dates = data["dates"]
    adata = data["assets"]
    betas = data["betas"]
    n = len(dates)

    target_weights = {sym: 1.0 / len(chosen_symbols) for sym in chosen_symbols}
    cash = 0.0

    def floor_pct(sym: str, i: int) -> float:
        return max(0.005, cfg.base_tol * betas[sym][i])

    def trigger_pct(sym: str, i: int) -> float:
        return max(0.005, cfg.base_tol * betas[sym][i])

    st: dict[str, dict[str, float]] = {}
    for sym in chosen_symbols:
        entry = adata[sym]["closes"][0]
        alloc = cfg.initial * target_weights[sym]
        qty, leftover = _buy_qty(sym, alloc, entry, cfg)
        fp = floor_pct(sym, 0)
        tp = trigger_pct(sym, 0)
        st[sym] = {
            "qty": qty,
            "floor": round(entry * (1 - fp), 2),
            "t_next": round(entry * (1 + tp), 2),
        }
        cash = round(cash + leftover, 2)

    def total_value(i: int) -> float:
        return round(cash + sum(st[sym]["qty"] * adata[sym]["closes"][i] for sym in chosen_symbols), 2)

    events: list[dict] = []
    history: list[dict] = []

    def evt(i: int, sym: str, action: str, price: float | None, amount: float | None, reason: str):
        if not record_events:
            return
        events.append(
            {
                "date": dates[i],
                "symbol": display(sym),
                "action": action,
                "price": round(price, 2) if price is not None else None,
                "amount": round(amount, 2) if amount is not None else None,
                "reason": reason,
            }
        )

    def snap(i: int):
        if not record_events:
            return
        history.append(
            {
                "date": dates[i],
                "assets": {display(sym): round(st[sym]["qty"] * adata[sym]["closes"][i], 2) for sym in chosen_symbols},
                "weights": {display(sym): round(target_weights[sym], 4) for sym in chosen_symbols},
                "cash": round(cash, 2),
                "total": total_value(i),
            }
        )

    def rebalance(i: int):
        nonlocal cash
        total = total_value(i)
        desired = {sym: total * target_weights[sym] for sym in chosen_symbols}

        for sym in chosen_symbols:
            price = adata[sym]["closes"][i]
            current_value = st[sym]["qty"] * price
            excess = current_value - desired[sym]
            if excess <= 0:
                continue
            sell_qty = excess / price if _is_fractional(sym, cfg) else math.floor(excess / price)
            if sell_qty <= 0:
                continue
            proceeds = round(sell_qty * price, 2)
            st[sym]["qty"] -= sell_qty
            cash = round(cash + proceeds, 2)
            evt(
                i,
                sym,
                "REBALANCE — sold",
                price,
                proceeds,
                f"Trimmed {display(sym)} by {sell_qty:.4f} sh to move toward target weight {target_weights[sym]:.2%}.",
            )

        deficits = []
        for sym in chosen_symbols:
            price = adata[sym]["closes"][i]
            current_value = st[sym]["qty"] * price
            gap = desired[sym] - current_value
            if gap > 0:
                deficits.append((gap, sym))
        deficits.sort(reverse=True)

        for _, sym in deficits:
            price = adata[sym]["closes"][i]
            current_value = st[sym]["qty"] * price
            gap = desired[sym] - current_value
            if gap <= 0 or cash <= 0:
                continue
            if _is_fractional(sym, cfg):
                spend = min(gap, cash)
                if spend <= 0:
                    continue
                qty = spend / price
            else:
                affordable = min(math.floor(gap / price), math.floor(cash / price))
                if affordable <= 0:
                    continue
                qty = affordable
                spend = round(qty * price, 2)
            st[sym]["qty"] += qty
            cash = round(cash - spend, 2)
            evt(
                i,
                sym,
                "REBALANCE — bought",
                price,
                spend,
                f"Bought {qty:.4f} sh of {display(sym)} to move toward target weight {target_weights[sym]:.2%}.",
            )

    for sym in chosen_symbols:
        entry = adata[sym]["closes"][0]
        evt(
            0,
            sym,
            "BUY",
            entry,
            cfg.initial * target_weights[sym],
            f"Initial purchase with target weight {target_weights[sym]:.2%}.",
        )
    snap(0)

    peak = total_value(0)
    max_dd = 0.0

    for i in range(1, n):
        for sym in chosen_symbols:
            close = adata[sym]["closes"][i]
            low = adata[sym]["lows"][i]
            s = st[sym]

            if low <= s["floor"]:
                stop_price = s["floor"]
                before, after = _shift_down(target_weights, sym, cfg.down_shift_pct)
                fp = floor_pct(sym, i)
                tp = trigger_pct(sym, i)
                s["floor"] = round(stop_price * (1 - fp), 2)
                s["t_next"] = round(stop_price * (1 + tp), 2)
                evt(
                    i,
                    sym,
                    "WEIGHT — reduced",
                    stop_price,
                    None,
                    f"{display(sym)} low ${low:,.2f} hit floor. Target weight {before:.2%} -> {after:.2%}; "
                    f"released weight redistributed equally across other assets. New floor ${s['floor']:,.2f}, next trail ${s['t_next']:,.2f}.",
                )
                continue

            if close >= s["t_next"]:
                old_floor = s["floor"]
                old_trigger = s["t_next"]
                before, after = _shift_up(target_weights, sym, cfg.up_shift_pct)
                s["floor"] = round(close * cfg.trail_stop, 2)
                s["t_next"] = round(close * cfg.trail_step, 2)
                evt(
                    i,
                    sym,
                    "WEIGHT — increased",
                    close,
                    None,
                    f"{display(sym)} cleared trail ${old_trigger:,.2f}. Target weight {before:.2%} -> {after:.2%}; "
                    f"added weight funded equally by other assets. Floor ${old_floor:,.2f} -> ${s['floor']:,.2f}; next trail ${s['t_next']:,.2f}.",
                )

        rebalance(i)
        total = total_value(i)
        peak = max(peak, total)
        if peak > 0:
            max_dd = max(max_dd, (peak - total) / peak)
        snap(i)

    init_qtys = {sym: (cfg.initial / len(chosen_symbols)) / adata[sym]["closes"][0] for sym in chosen_symbols}
    bh_final = round(sum(init_qtys[sym] * adata[sym]["closes"][-1] for sym in chosen_symbols), 2)
    final = total_value(n - 1)

    return {
        "dates": dates,
        "events": events,
        "history": history,
        "final": final,
        "return_pct": round((final - cfg.initial) / cfg.initial * 100, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "bh_final": bh_final,
        "bh_return_pct": round((bh_final - cfg.initial) / cfg.initial * 100, 2),
        "final_weights": {display(sym): round(target_weights[sym], 4) for sym in chosen_symbols},
        "symbols": [display(sym) for sym in chosen_symbols],
    }
