# RESULTS.md

Research results and lessons learned for `~/Dev/trading`.

This file is the current research handoff. It focuses on:

- what we tested
- what worked and what did not
- the main hiccups we encountered
- the current conclusion

## Scope

The repo now has two distinct research modes that should not be confused:

- benchmark mode: train on `2023-01-01` through `2023-12-31`, then test on the 9-quarter holdout `2024-01-02` through `2026-03-31`
- production-refit mode: fit on all available history through `2026-04-01` to generate live bot defaults after the benchmark work is already understood

Core basket:

- `TSLA`
- `TSM`
- `NVDA`
- `PLTR`
- `BTC/USD`

The benchmark results below are the most trustworthy strategy comparison results in the repo. The production refit is useful for live deployment, but it is in-sample and not a fair measure of out-of-sample edge.

Current engine note:

- the simulator now executes stop sells on the next tradable bar using the worse of the stop floor and the next tradable open
- the default simulator path now uses fractional stock sizing to better match the live bot's notional stock orders
- the default simulator path now assumes Alpaca-style settlement: crypto proceeds settle immediately, while stock sale proceeds release on the next trading day
- the default simulator path now blocks tiny churn with explicit minimum rebalance and minimum order notionals
- the benchmark and refit artifacts cited below were rerun after those changes

## Main Results

### 1. Buy-And-Hold Still Leads On The Corrected Holdout

Under the current Alpaca-aligned realism model, buy-and-hold still beat both tested active variants on the `2024-01-02` through `2026-03-31` holdout.

Latest friction-enabled holdout result from [hourly_strategy_results.json](/Users/ecohen/Dev/trading/hourly_strategy_results.json):

- buy-and-hold: `$29,108.90`
- rebalance-only: `$26,399.08`
- stop/trigger + rebalance: `$25,045.13`

Conclusion:

- the corrected engine no longer shows a strong active edge over passive hold-and-sit

### 2. Stop / Trigger Lost Its Earlier Edge After The Realism Sprint

Current best 2023-trained hourly config for the benchmark routine:

- `base_tol = 0.0348`
- `stop_sell_pct = 0.1158`
- `trail_step = 1.0080`
- `trail_stop = 0.9691`
- `stop_cooldown_days = 5`

Latest friction-enabled 9-quarter holdout:

- final: `$25,045.13`
- return: `+150.45%`
- max drawdown: `38.59%`
- turnover: `$304,236.17`
- buy-and-hold: `$29,108.90`

Interpretation:

- the earlier stop/trigger outperformance was largely an engine artifact
- after next-bar stops, minimum trade thresholds, and Alpaca-style settlement, the optimized stop variant no longer beats either buy-and-hold or rebalance-only on holdout
- turnover is still materially higher than the simpler rebalance path

Important scope note:

- this benchmark was rerun under the current live profile of `TSLA 50%` and `TSM/NVDA/PLTR/BTC 12.5%` each
- it is the current best apples-to-apples benchmark in the repo

### 3. Weight Shifting Did Not Help

We tested a variant where stop hits reduced target weight and upper triggers increased target weight, with rebalance only at the close.

Files:

- [weight_shift_strategy.py](/Users/ecohen/Dev/trading/weight_shift_strategy.py)
- [optimize_weight_shift.py](/Users/ecohen/Dev/trading/optimize_weight_shift.py)

Result:

- trained variants could look good in-sample
- on holdout, they underperformed the simpler baseline

Conclusion:

- weight shifting is not part of the current recommendation

### 4. Beta Scaling Stayed In

We tested the baseline with and without beta scaling.

Result:

- beta scaling remains useful as a stabilizing mechanic inside the current simulator
- but it should not be mistaken for proof that the active strategy beats passive exposure

Conclusion:

- keep beta scaling for now

## Buffer Findings

### Cash Buffer

We retested the strategy with a plain cash buffer, so proceeds stayed in cash instead of being auto-parked into BTC.

Result:

- after the realism sprint, the same cash-buffer structure no longer produced absurd benchmark numbers
- updated live-weight holdout:
- rebalance-only: `$26,399.08`
- stop/trigger + rebalance: `$25,045.13`
- updated full-history refit best train: `$61,204.53`

Interpretation:

- the earlier cash-buffer explosions were mostly engine artifacts
- the corrected model is far more believable, but it also removes the illusion of a huge active edge

Conclusion:

- cash buffer is still the right live structure for simplicity and cleaner exposure
- but under the corrected engine it does not currently justify an aggressive active strategy on benchmark evidence alone

## Friction Model

We added a first-pass friction model to the hourly simulator in [hourly_strategy.py](/Users/ecohen/Dev/trading/hourly_strategy.py) and optimizer in [optimize_hourly_strategies.py](/Users/ecohen/Dev/trading/optimize_hourly_strategies.py).

Current assumptions:

- stock slippage: `5 bps`
- crypto slippage: `10 bps`
- crypto taker fee: `25 bps`
- equity SEC sell fee rate: `0.00002060`
- equity TAF: `0.000195/share`, capped at `$9.79`
- equity CAT: `0.000046/share`

Why:

- Alpaca stock trading is generally commission-free, but regulatory fees still apply
- Alpaca crypto trading is not fee-free
- real execution is not frictionless even when commissions are zero

Important note:

- first-pass friction is now sitting on top of more realistic execution and settlement assumptions
- the exact return numbers still should not be over-interpreted, but they are much less obviously fantasy-driven than before

Conclusion:

- keep the friction model
- do not over-interpret the exact uplift from the current implementation

## Data Caching

The hourly simulator originally cached market data only in memory, so every new script run asked Alpaca for the same bars again.

Fix:

- added a persistent disk cache in [hourly_strategy.py](/Users/ecohen/Dev/trading/hourly_strategy.py)
- raw hourly bars are now stored under `.cache/hourly_data/`
- cache files are local-only and ignored by git

Result:

- reruns are much faster
- research is more reproducible
- we do not keep hammering Alpaca for the same 2023-2026 data on every process start

## Production Refit

After the benchmark work, we added a separate full-history refit flow for live deployment in [refit_bot_strategy.py](/Users/ecohen/Dev/trading/refit_bot_strategy.py).

Current production refit artifact:

- [bot_refit_results.json](/Users/ecohen/Dev/trading/bot_refit_results.json)
- the artifact now keeps the in-sample winner under `best_train` only and adds an explicit `live_default_policy.auto_promote = false`

Search setup:

- train: `2023-01-01` through `2026-04-01`
- target weights: `TSLA 50%`, `TSM/NVDA/PLTR/BTC 12.5%` each
- buffer mode: `cash`
- same first-pass friction model as the benchmark optimizer

Current optimizer winner from that refit:

- `base_tol = 0.0348`
- `stop_sell_pct = 0.1158`
- `trail_step = 1.0080`
- `trail_stop = 0.9691`
- `stop_cooldown_days = 5`

Important caution:

- the full-history refit is in-sample only
- its raw train result is now far more sane than the old pathological artifact, but it still underperforms buy-and-hold on the same full-history window
- it is useful as a parameter-generation step for the live bot, not as a replacement for the 2023 / 9-quarter benchmark
- because the holdout still does not beat buy-and-hold, the live bot defaults should stay conservative and should not auto-promote the in-sample refit winner

## Major Hiccups And Fixes

### 1. Unrealistic Hourly Results From Early Engine Versions

Early hourly stop/trigger results were far too strong to trust.

Main causes we found:

- using data in a way that did not properly account for stock splits
- BTC core / BTC buffer accounting errors in the earlier BTC-buffer engine
- over-generous frictionless trade assumptions

Fixes:

- switched to Alpaca adjusted stock bars
- corrected BTC core vs buffer accounting in the earlier engine, then later removed buffer-via-BTC entirely in favor of cash
- separated stock-session rebalance from crypto 24x7 monitoring
- later added first-pass friction

### 2. Rebalance Frequency Was Too Aggressive

An early version rebalanced too often intraday, which created extreme churn and unrealistic turnover.

Fix:

- changed rebalance to once per trading day, on the last stock-session bar

### 3. Live Bot And Simulator Were Not Identical

We found an important behavior gap:

- live bot blocks same-day re-entry after a trade
- simulator originally only blocked repeat trades within the same bar

This matters because same-day close re-entry can materially change outcomes.

Fix:

- we made the difference explicit in analysis
- we compared both policies side by side instead of assuming they matched

Result:

- in the Jan-Feb 2024 comparison, the same-day guard reduced turnover a lot and slightly improved final value in that short window
- we still kept the no-same-day-guard simulator path as the default research branch for the 2023/9Q routine unless otherwise specified

### 4. Reporting Window End-Boundary Artifacts

A chart for the first few weeks of 2024 was inconsistent with an earlier one.

Cause:

- too-tight data cutoff at the end of the reporting window

Fix:

- reload with a buffer beyond the visible display window before computing end-of-day values

### 5. Cooldown Semantics Needed Clarification

We clarified that the live bot cooldown is in trading days, not calendar days.

Current live behavior:

- after a stop, skip the next `N` trading sessions
- become stop-eligible again on the following trading session

## Live Bot Status

The live bot in [bot.py](/Users/ecohen/Dev/trading/bot.py) has been updated to align more closely with the current best practical strategy:

- partial stop sells
- trading-day cooldown
- cash-buffer bookkeeping
- end-of-day rebalance

But the live bot is still not identical to the simulator.

Key current difference:

- the live bot now monitors crypto 24x7 and keeps proceeds in cash rather than parking them into BTC

See [TODO.md](/Users/ecohen/Dev/trading/TODO.md) for remaining live-bot decisions.

## Current Conclusion

Best practical strategy currently trusted in this repo:

- 5-asset basket
- weighted target profile
- `TSLA 50%`, `TSM/NVDA/PLTR/BTC 12.5%` each in the current live profile
- beta-scaled stop floors
- trailing floor raises
- partial stop sales
- cash buffer
- daily rebalance near the close

Things currently not recommended:

- pure rebalance as the main strategy
- weight shifting
- cash-buffer optimizer output as a trusted improvement

## Confidence / Caution

What we believe:

- the stop/trigger + rebalance structure is the strongest idea tested so far
- the repo is in much better shape than the first hourly experiments
- the disk cache and explicit production-refit flow make the research loop much more reproducible

What we do not yet believe:

- that the highest raw backtest outputs should be treated as deployable at face value
- that a frictionless or near-frictionless replay is enough evidence for live deployment
- that the full-history refit should be treated like out-of-sample proof

Next sensible validation steps:

- rerun the 2023 / 9-quarter benchmark under the new weighted live profile
- add repeatable walk-forward validation
- add more realistic execution assumptions if needed
- compare BTC-buffer and cash-buffer only under tightly constrained, realism-focused rules
