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

## Handoff Summary

- CapitolTrades research: Markwayne Mullin was the best default politician to follow for an MVP copy-trade bot because he was active into 2026 and CapitolTrades highlighted his strong 2025 results. Important caveat: CapitolTrades is disclosure-based, so any copier would mirror `published` trades, not real-time execution.
- CapitolTrades sizing data is approximate, not exact. The site usually provides ticker, side, trade date, publish date, ownership type, and a size band like `50K-100K` or `100K-250K`, sometimes with price/share ranges. Exact mirroring is therefore unrealistic.
- Copy-trade prototype files were added in `copytrade_demo.py` and `copytrade_signals.json` to demonstrate a simpler approach: filter to larger disclosed stock trades, act on `published_at`, and convert qualifying disclosures into target weights rather than guessed exact dollars.
- The main sandbox strategy remains the best-performing practical approach tested so far: equal-weight `TSLA`, `TSM`, `NVDA`, `PLTR`, `BTC-USD`; beta-scaled stop floors; trailing floor raises; partial stop sales into the BTC buffer; end-of-day rebalance.
- We clarified the baseline control flow: if an asset hits its stop, the sale happens that day, proceeds are parked in the BTC buffer, and that same asset is generally not repurchased until the next trading day or later because of the one-trade-per-asset-per-day rule.
- We tested a new weight-shift idea in `weight_shift_strategy.py` and `optimize_weight_shift.py`: on stop hit, reduce target weight by `X%` and redistribute equally to the other assets; on upper trigger, add `Y%` to target weight; only rebalance at the close.
- Weight shifting did not help. On the `2023` train / `2024-01-02` through `2026-03-31` holdout split, the trained `X/Y` variants underperformed the simple `X=0, Y=0` baseline in both whole-share mode and fractional-stock Alpaca-style mode.
- Fractional-stock Alpaca-style testing did not change that conclusion. The best trained fractional result was still around `X=0%, Y=5%`, but holdout performance remained worse than the plain baseline.
- We also tested beta scaling versus forcing every beta to `1.0`. Beta scaling helped on the `2023` training window but did not beat the no-beta version on the `2024-01-02` through `2026-03-31` holdout. Still, beta scaling was much calmer with far fewer stops/trails, so we chose to keep it.
- Broker conclusion for this repo: Alpaca is the cleanest fit because it supports API automation, fractional shares, and direct `BTC/USD` trading in both paper and live environments. Schwab supports BTC exposure via ETFs but not direct spot BTC in the brokerage account.
- Current recommendation for the next thread: keep the original baseline structure, keep beta scaling, drop the weight-shifting branch as a trading improvement idea, and treat the CapitolTrades work as a separate experimental track rather than the main strategy.
