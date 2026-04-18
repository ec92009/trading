# TODO

## Live Bot

- Improve BTC rebalance buys so they use available cash more reliably on Alpaca paper accounts.
- Decide whether BTC should keep following the stock-session clock in the live bot, or whether its stop/trail logic should run 24x7 now that the sandbox strategy treats BTC as both a core holding and the buffer asset.
- Decide whether same-day startup rebalance should be allowed again near the close, or whether one rebalance per day is the desired live rule.
- Handle dust positions more explicitly so tiny leftovers like the residual `AAPL` share do not trigger repeated cleanup attempts.
- Consider using open-order and filled-order reconciliation before rebalance buys so the bot does not rely on a fixed wait after sells.
- Investigate the next steps to run a real-money bot against the user's Robinhood account with very limited funds, including broker/API feasibility, order constraints, risk controls, and operational safeguards before any live deployment.
- Write and validate a cash injection / withdrawal SOP so deposits, withdrawals, and balance changes do not confuse target weights, performance reporting, or rebalance logic.

## Strategy Validation

- Rerun the benchmark `2023` / 9-quarter routine under the current `TSLA 50%` live weight profile so the live allocation has a clean holdout record too.
- Compare corrected fractional-stock results directly against whole-share results on the same train/test windows.
- Add a repeatable walk-forward validation flow instead of one-off date-range experiments.
- Add turnover and event-count summaries to optimizer output so extreme results are easier to sanity-check.
- Decide whether optimizer output should continue to overwrite `optimizer_results.json` or whether date-stamped result files would be better.
