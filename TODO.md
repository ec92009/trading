# TODO

## Live Bot

- Improve BTC rebalance buys so they use available cash more reliably on Alpaca paper accounts.
- Decide whether same-day startup rebalance should be allowed again near the close, or whether one rebalance per day is the desired live rule.
- Handle dust positions more explicitly so tiny leftovers like the residual `AAPL` share do not trigger repeated cleanup attempts.
- Consider using open-order and filled-order reconciliation before rebalance buys so the bot does not rely on a fixed wait after sells.

## Strategy Validation

- Compare corrected fractional-stock results directly against whole-share results on the same train/test windows.
- Add a repeatable walk-forward validation flow instead of one-off date-range experiments.
- Add turnover and event-count summaries to optimizer output so extreme results are easier to sanity-check.
- Decide whether optimizer output should continue to overwrite `optimizer_results.json` or whether date-stamped result files would be better.

## Documentation

- Add a link from `README.md` to `STRATEGY.md`.
- Document the live bot's new behavior separately from the sandbox simulator behavior so the two are not confused.
