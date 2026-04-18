# TODO

## Live Bot

- Continue simplifying the codebase around the current long-term assumptions: Alpaca as the broker path, fractional stocks as the default, and fewer broker-agnostic compatibility leftovers.
- Decide whether the live bot should support more than one crypto symbol now that off-hours monitoring is keyed off asset class instead of a hardcoded BTC path.
- Decide whether same-day startup rebalance should be allowed again near the close, or whether one rebalance per day is the desired live rule.
- Handle dust positions more explicitly so tiny leftovers like the residual `AAPL` share do not trigger repeated cleanup attempts.
- Consider using open-order and filled-order reconciliation before rebalance buys so the bot does not rely on a fixed wait after sells.
- Investigate the next steps to run a real-money bot against the user's Robinhood account with very limited funds, including broker/API feasibility, order constraints, risk controls, and operational safeguards before any live deployment.
- Write and validate a cash injection / withdrawal SOP so deposits, withdrawals, and balance changes do not confuse target weights, performance reporting, or rebalance logic.

## Strategy Validation

- Run and document a direct fractional-vs-whole-share comparison on the same train/test windows now that live-parity fractional stocks are the default simulator path.
- Decide whether any active variant still deserves live attention now that the corrected `2023` / 9-quarter benchmark leaves buy-and-hold in front.
- Add a repeatable walk-forward validation flow instead of one-off date-range experiments.
- Add turnover and event-count summaries to optimizer output so extreme results are easier to sanity-check.
- Decide whether optimizer output should continue to overwrite `optimizer_results.json` or whether date-stamped result files would be better.
