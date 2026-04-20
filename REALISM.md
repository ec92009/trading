# REALISM.md

Notes for making the simulator and live bot more realistic before trusting more strategy changes.

## Why This Matters

Recent cash-buffer reruns showed a familiar failure mode:

- enormous turnover on a small account
- extremely tight trailing parameters
- zero-day cooldown winners
- implausibly smooth equity curves despite constant trading

That strongly suggests the engine is still too generous in a few key places.

## Highest-Priority Fixes

### 1. Stop Execution Realism

Status:

- implemented in the simulator
- stop breaches now arm a sale for the next tradable bar
- the fill uses the worse of the stop floor and the next tradable bar open

Current issue:

- stop sells are still triggered from hourly bar lows rather than true intrabar trade data

Better options:

- current implementation: trigger on breach, execute at next bar open
- current implementation detail: use the worse of stop floor and next tradable price
- future upgrade option: add extra adverse slippage on stop fills during large gaps

This was the single most important realism upgrade and is now in place.

### 2. Turnover Throttles

Status:

- implemented in the simulator with a minimum rebalance notional and minimum order notional

Add explicit trading frictions beyond fees:

- minimum rebalance threshold before any trade is allowed
- minimum order size in dollars
- minimum remaining position value
- one stop per symbol per bar or per day

These should reduce optimizer wins that come purely from microscopic churn.

### 3. Settled vs Unsettled Cash

Status:

- implemented in the simulator with an Alpaca-aware settlement model
- crypto sale proceeds settle immediately
- stock sale proceeds release on the next trading day

Current issue:

- this is now much better, but still conservative and simplified compared with live account buying-power rules

Needed model:

- explicit settled cash
- unsettled proceeds bucket
- broker-specific reuse rules for stock and crypto buying power

This matters especially if we want realistic cash-account behavior.

## Second-Priority Fixes

### 4. Trade Frequency Caps

Add strategy-level safety constraints:

- max stop events per symbol per day
- max total trades per day
- max daily turnover as a percentage of equity

These help keep optimizer winners broker-compatible.

### 5. Same-Day Re-entry Policy

Treat this as a first-class parameter, not an incidental implementation detail.

We should compare at least:

- `same_day_reentry = false`
- `same_day_reentry = true`

This choice materially changes turnover and outcomes.

### 6. Asset-Class Schedules

Avoid hardcoding `BTC/USD` as the only 24x7 asset.

The engine should treat:

- crypto symbols as 24x7
- stocks as exchange-session instruments

The repo has already started moving in this direction and should keep going.

## Live Bot Safety Rails

Before pushing any more aggressive strategy live, the bot should have:

- autonomous Capitol signal refresh is now in place for the Ro Khanna `10K` path, so the remaining safety work is mostly broker / execution hardening rather than manual-data plumbing
- max daily turnover guard
- max daily realized loss guard
- max orders per symbol per day
- repeated-rejection kill switch
- cleaner reconciliation of open orders, filled orders, positions, and buying power
- explicit dust-position handling

## Recommended Next Sprint

If we only do one realism pass next, do these three:

1. trade-frequency caps or turnover guards
2. same-day re-entry policy comparison
3. a fuller Alpaca buying-power model if the conservative settled-cash version proves too harsh

These three changes should tell us quickly whether the strategy still has real edge once the backtest becomes harder to exploit.

## Working Expectation

Most likely:

- the giant cash-buffer outputs collapse
- the strategy may still work in a milder form
- if it survives, the result will be much more trustworthy and much easier to deploy safely
