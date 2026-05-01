# TODO

## Live Bot

- Continue simplifying the codebase around the current long-term assumptions: Alpaca as the broker path, fractional stocks as the default for TeslaBot research, and fewer broker-agnostic compatibility leftovers.
- Decide how much more TeslaBot cleanup is worth doing now that it is the old ~$350 Alpaca basket bot rather than the main production path.
- Decide whether the autonomous Capitol refresh path should stay Khanna-only or become a reusable framework for other politician CopyBot-style politician bots too.
- Decide whether `copytrade_signals.json` should remain the canonical merged signal file or whether politician-specific cached snapshots under `/_cache/politicians/` should become first-class live artifacts.
- Decide whether additional politician refresh jobs should be turned on in CopyBot now that yearly politician caches exist for Khanna, Mullin, Gottheimer, Hern, and Taylor.
- Decide whether CopyBot should support more than one crypto symbol now that off-hours monitoring is keyed off asset class instead of a hardcoded BTC path.
- Decide whether same-day startup rebalance should be allowed again near the close, or whether one rebalance per day is the desired TeslaBot rule.
- Handle TeslaBot dust positions more explicitly so tiny leftovers like the residual `AAPL` share do not trigger repeated cleanup attempts.
- Consider using open-order and filled-order reconciliation before TeslaBot rebalance buys so the bot does not rely on a fixed wait after sells.
- Investigate the next steps to run a real-money CopyBot against the user's Robinhood account with very limited funds, including broker/API feasibility, order constraints, risk controls, and operational safeguards before any live deployment.
- Write and validate a CopyBot cash injection / withdrawal SOP so deposits, withdrawals, and balance changes do not confuse target weights, performance reporting, or rebalance logic.

## Strategy Validation

- Compare the live-weight basket directly against `SPY` across walk-forward windows so we know whether the basket itself is earning its extra concentration risk.
- Run and document a direct fractional-vs-whole-share comparison on the same train/test windows now that live-parity fractional stocks are the default simulator path.
- Decide whether any active basket variant still deserves TeslaBot attention now that the corrected `2023` / 9-quarter benchmark leaves basket buy-and-hold in front.
- Decide whether `stop/trigger only` is worth keeping as a formal defensive mode now that it cuts drawdown sharply but lags even `SPY` on return.
- Repeat [walk_forward_hourly.py](/Users/ecohen/Dev/trading/walk_forward_hourly.py) on the more balanced candidate baskets we tested manually, not just the live-weight basket.
- Add turnover and event-count summaries to optimizer output so extreme results are easier to sanity-check.
- Decide whether optimizer output should continue to overwrite `optimizer_results.json` or whether date-stamped result files would be better.

## Capitol Research

- Decide whether high-decay Mullin is acceptable as a concentrated latest-event strategy, or whether a live-style version needs a minimum diversification rule even if it lowers backtest return.
- Backfill Josh Gottheimer and compare his actionable publication history and symbol cleanliness against Mullin before committing to a single Capitol source.
- Decide whether CopyBot's live `60`-day half-life should stay intentionally smoother than the shorter-memory research winners, or whether CopyBot should move closer to the stronger but more twitchy research settings.
