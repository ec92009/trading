# Research Handoff

Current state for `~/Dev/trading` as of `2026-04-19`.

## Core Bot / Basket

- The simulator realism sprint materially reduced the old active-strategy edge.
- The important realism fixes now in place are:
- next-bar, gap-aware stop execution
- Alpaca-style settlement timing
- live-parity fractional stock sizing
- minimum trade thresholds
- shared quarterly symbol cache files instead of exact-window cache files
- Under the current corrected benchmark setup, the serious contenders for the live 5-name basket are now:
- basket buy-and-hold
- rebalance-only
- Stop-heavy variants no longer lead out of sample.
- Current live posture is intentionally calmer:
- `TSLA 50%`
- `TSM 12.5%`
- `NVDA 12.5%`
- `PLTR 12.5%`
- `BTC/USD 12.5%`
- live execution stays rebalance-only for now

## Benchmark Convention

- Main basket benchmark mode:
- train: `2023-01-01` through `2023-12-31`
- holdout: `2024-01-02` through `2026-03-31`
- Walk-forward validation now exists and is the better cross-window read than a single long holdout.

## Capitol / Mullin Direction

- Capitol now looks more interesting than the old stop-overlay path as a source of actual stock-picking edge.
- We now have a broader local Capitol dataset, not just Mullin.
- Mullin actionable history is short:
- actionable `published_at` window starts `2025-08-13`
- current local actionable history runs through `2026-03-10`
- For standalone Mullin research, the working split is:
- train: `2025-08-13` through `2025-12-31`
- test: `2026-01-01` through `2026-04-19`
- The Capitol simulator is daily-policy research built on Alpaca hourly cache data:
- decisions are keyed off `published_at`
- entries use the next trading day open
- positions use Alpaca-style fractional sizing

## Local Capitol Dataset

Current merged signal file now contains:

- `Ro Khanna`: `9101` rows
- `Josh Gottheimer`: `1381` rows
- `Kevin Hern`: `176` rows
- `David Taylor`: `169` rows
- `Markwayne Mullin`: `115` rows

Total rows in [copytrade_signals.json](/Users/ecohen/Dev/trading/copytrade_signals.json): `10942`

Current read on who to test next after Mullin:

- `Josh Gottheimer`
- `Kevin Hern`
- `David Taylor`
- `Ro Khanna`

Reasoning:

- Gottheimer looks like the clearest next large-cap / meaningful-size candidate.
- Hern looks like a cleaner real-money style profile than the ultra-busy names.
- Taylor looks small but clean and concentrated, which is useful for framework validation.
- Khanna is the high-upside stress test, but also by far the noisiest profile.

## Capitol Weighting Rules

- We dropped the old `literal` copy mode.
- Active names now use a point-based target system:
- `5M-25M`: `20` points
- `50K+` disclosure bands: `4` points
- `15K-50K`: `2` points
- `1K-15K`: `1` point
- `'< 1K'`: ignored
- Target weight is `symbol points / total active points`.

## Capitol Queue Rules

- The current Mullin simulator now applies a capped exit queue.
- Default queue limit is `10` names.
- Point behavior:
- repeated buys stack on top of the current live balance
- sells subtract their disclosure-tier points instead of hard-resetting a name
- point balances floor at `0`
- point balances can decay by a configurable daily percentage between event days
- Queue behavior:
- lower bands sit closer to the exit
- higher bands sit farther from the exit
- within the same band, weaker `%` performers move closer to the exit
- within the same band, stronger `%` performers stay farther from the exit
- older entry order still breaks ties when same-band performance is equal
- same-day top-tier / band-1 bursts can temporarily expand the working queue above the base cap
- after a burst, the working queue limit steps back down by `1` on later event days until it returns to the base cap
- when a new name causes overflow, eviction happens from the front of the queue
- Practical implication:
- weak bands can enter, but they are first to be crowded out
- same-band losers can now drift toward the exit even if they entered earlier with the same points
- high daily decay can effectively turn the model into a “latest event matters most” engine
- large top-tier / band-1 bursts no longer force an immediate arbitrary trim just because they exceed the base cap on that event day

## Latest Mullin Results

All results below use fresh `$10,000` cash in each window, daily policy, next-trading-day execution, fractional shares, the current stacked-point queue logic, and daily point decay `x` where applicable.

### Mullin Decay Sweep

Working Mullin split:

- train: `2025-08-13` through `2025-12-31`
- test: `2026-01-01` through `2026-04-19`
- full local actionable window through `2026-04-19`

#### `50K-100K+` Mullin

| Daily Decay `x` | Train Return | Test Return | Full-Window Return | SPY Test |
|---|---:|---:|---:|---:|
| `0.00` | `+64.47%` | `+5.12%` | `+57.03%` | `+4.37%` |
| `0.65` | `+74.32%` | `+9.96%` | `+84.75%` | `+4.37%` |
| `1.00` | `+74.32%` | `+9.96%` | `+84.75%` | `+4.37%` |

Interpretation:

- Large-signal Mullin clearly prefers very high decay.
- In practice, `x = 0.65` and `x = 1.00` behave the same on returns.
- That means old `50K+` Mullin points are effectively dead by the next event anyway.
- The practical takeaway is not “exactly `1.00` is magic,” but “large Mullin disclosures have very short memory.”

#### `15K-50K+` Mullin

| Daily Decay `x` | Train Return | Test Return | Full-Window Return | SPY Test |
|---|---:|---:|---:|---:|
| `0.00` | `+49.63%` | `+9.91%` | `+46.74%` | `+2.65%` |
| `0.65` | `+48.99%` | `+14.83%` | `+67.97%` | `+2.65%` |
| `1.00` | `+25.26%` | `+14.83%` | `+41.22%` | `+2.65%` |

Interpretation:

- Medium-band Mullin does not want zero decay anymore.
- `x = 0.65` is the best current compromise:
- test improves materially versus `x = 0.00`
- full-window return also improves materially versus `x = 0.00`
- unlike `x = 1.00`, it does not crush the train window or the full window
- `x = 1.00` turns the model into an almost pure “latest event only” engine, which helps this short test but is too extreme across the broader window.

### Queue Length Check

We also retested the current decay setups with a `20`-slot queue instead of the default `10`.

Interpretation:

- For `50K+` Mullin, queue length barely matters because the active book is already small.
- For `15K-50K+` Mullin, `20` slots is worse than `10`:
- with `x = 0.00`, test falls from `+9.91%` to `+8.33%`
- with `x = 0.65`, test falls from `+14.83%` to `+13.28%`
- with `x = 1.00`, test falls from `+14.83%` to `+13.28%`
- The extra capacity mostly lets weaker medium-band names linger instead of forcing concentration into the best Mullin names.

### Khanna `50K+` Results

Working Khanna split:

- train: `2024-02-07` through `2025-12-31`
- test: `2026-01-01` through `2026-04-19`
- full local actionable window through `2026-04-19`
- eligible sample: `183` signals across `83` symbols

Queue-size sweep read:

- Queue sizes `10`, `15`, and `20` are effectively identical on the current Khanna run.
- Test return is flat at about `+11.13%` to `+11.14%`.
- Full-window return is also effectively flat once decay is in the good range.
- Practical takeaway: a soft queue limit of `10` is enough.

Burst trace read:

- The scary `83` names are over the full history, not all at once.
- The largest simultaneous active queue in the current best Khanna setup is `16` names.
- That peak happens on `2024-07-10`.
- The queue-expansion logic works:
- the limit temporarily expands above `10`
- then steps back down by `1` on later event days until it returns to the base cap
- Because the true burst is only `16`, a hard move to `20` slots buys almost nothing.

### Half-Life Framing

The current Capitol decay parameter is best understood as an event-driven model with calendar-day memory:

- decay is only applied when the simulator advances from one event trade day to the next event trade day
- the gap uses calendar days between those event days

That makes half-life a cleaner unit than raw daily decay percent.

### Khanna Half-Life Sweep

For `Ro Khanna` `50K+` with the soft `10`-slot queue:

- The best region is a short-memory plateau around `1.25` to `2.0` calendar days.
- The practical default is `2` calendar days.
- That corresponds to daily retention of about `70.71%` and daily decay of about `29.29%`.

Interpretation:

- Khanna wants forgetting, but not the near-instant memory death that Mullin likes.
- No-decay Khanna is materially worse than the short-memory settings.
- Longer half-lives steadily degrade while usually increasing transaction count.

### Mullin Half-Life Sweep

We reran Mullin in the same half-life framing with the soft `10`-slot queue.

Interpretation:

- `50K+` Mullin prefers about `1` calendar day or shorter.
- `15K-50K+` Mullin also prefers about `1` calendar day or shorter.
- In the `1` to `15` day grid, `1` day is the best visible point for both Mullin bands.
- The earlier `x = 0.65` Mullin result implies an even shorter half-life of about `0.66` days, so the true Mullin optimum may be slightly shorter than `1` day.

Practical takeaway:

- Khanna and Mullin do not want the same memory.
- Current defaults:
- `Ro Khanna 50K+`: about `2` calendar days
- `Markwayne Mullin 50K+`: about `1` calendar day

### Other Whale Checks

We sanity-checked the same `50K+` half-life idea on other Capitol whales:

- `Kevin Hern` `50K+` lines up more with Khanna than Mullin and prefers about `2` days.
- `Josh Gottheimer` `50K+` is not informative on decay because his large-signal sample is almost entirely one symbol (`MSFT`), so many half-lives behave the same.
- `David Taylor` has no `50K+` sample.

Interpretation:

- There is not a universal whale half-life law.
- Broad multi-name whale profiles currently lean closer to `2` days.
- Mullin still looks like the short-memory outlier.

### Merged Whale Read

We also looked at the merged `50K+` feed across:

- `Josh Gottheimer`
- `Kevin Hern`
- `David Taylor`
- `Ro Khanna`
- `Markwayne Mullin`

Signal-level read:

- merged sample: `288` qualifying `50K+` signals across `106` symbols
- the merged book is still mostly dominated by `Ro Khanna`
- the largest burst is still basically Khanna's own `2024-07-09` cluster
- genuine same-day, same-symbol cross-politician reinforcement is almost nonexistent
- the only clear cross-politician same-symbol event in the merged `50K+` set is `MSFT` on `2025-05-15` from `Josh Gottheimer` and `Ro Khanna`

Market-data read:

- the merged-whale cache now has path coverage for all expected symbol-quarter files
- but some symbol-quarters are still empty Alpaca caches, especially on names like `SPX`, `BNPQY`, `GSKPX`, `NTIOF`, `MAIPX`, `NCR`, and a few other non-plain-vanilla symbols
- several of those names do carry real `50K+` whale signals, mostly from Khanna and some from Kevin Hern

Practical takeaway:

- The merged-whale idea does not currently look like a strong consensus overlay.
- It mostly behaves like Khanna's wide book plus a few isolated reinforcements.
- Any merged-whale PnL result should still be treated cautiously until the unsupported / empty-cache symbols are handled more deliberately.

## Current Conclusion

- For the main 5-name basket, buy-and-hold and rebalance-only remain the only credible live postures under the corrected simulator.
- For Capitol, Mullin still looks promising as a standalone normalized strategy, but the edge is modest and the actionable history is short.
- The strongest new Mullin finding is that explicit point decay matters more than the older queue-only tweaks.
- For `50K+` Mullin, the strategy wants very high decay, which effectively says the latest large event dominates the older ones.
- For `15K-50K+` Mullin, `x = 0.65` is the best current practical default:
- materially better than `x = 0.00` on test and full-window returns
- better balanced than `x = 1.00`
- A `10`-slot queue still looks better than `20` for Mullin.
- In half-life terms, the current practical memory defaults are:
- `Ro Khanna 50K+`: about `2` calendar days
- `Markwayne Mullin 50K+`: about `1` calendar day
- Khanna's big universe is much less threatening than it first appears because the real simultaneous burst is only about `16` names under the current queue logic.
- Merged-whale overlap is weaker than expected, so a naive combined whale source is not obviously better than just using Khanna or Mullin directly.
- Lower Mullin disclosure bands are less dangerous once decay and queue discipline are active, but the cleaner read still comes from focusing on the more meaningful signals.

## Most Useful Next Steps

- Sanity-check the Mullin event trace under the roughly `1`-day half-life framing so we understand whether the strong result is intuitive or just a side effect of near-total concentration in the latest event.
- Tighten the Mullin half-life grid below `1` calendar day so we can decide whether the cleaner default should stay at `1` day or move closer to the earlier `x = 0.65` result.
- Decide whether Khanna should be the default Capitol paper-trading candidate now that the soft `10`-slot queue and `~2`-day half-life look stable.
- Handle the unsupported / empty-cache merged-whale symbols more explicitly before treating any merged-whale PnL as a final answer.
- If merged whales stay interesting, compare a strict tradable-symbol merged feed against the raw merged feed to measure how much the weird Alpaca symbols are distorting the idea.
