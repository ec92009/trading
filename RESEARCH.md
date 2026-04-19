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

### Khanna Status

- We narrowed the next follow-up to `Ro Khanna` `50K+` only.
- That still means `183` qualifying signals across `83` symbols, so the first pass is much more cache-heavy than Mullin.
- We do have the signal data needed for the queue-size / decay study.
- The remaining blocker is market-data warmup, not missing Capitol data.
- The hourly cache layout has now been reorganized to a cleaner quarter tree:
- `symbols/YYYY/Qn/SYMBOL.json`
- legacy hashed cache files are still readable during the transition
- a migration helper now exists so old cache files can be copied into the new layout without refetching market data
- Once the Khanna cache warm finishes, the intended first Khanna sweep is:
- queue sizes `10`, `15`, `20`
- daily decay `0.25`, `0.50`, `0.75`, `1.00`
- `50K+` only

## Current Conclusion

- For the main 5-name basket, buy-and-hold and rebalance-only remain the only credible live postures under the corrected simulator.
- For Capitol, Mullin still looks promising as a standalone normalized strategy, but the edge is modest and the actionable history is short.
- The strongest new Mullin finding is that explicit point decay matters more than the older queue-only tweaks.
- For `50K+` Mullin, the strategy wants very high decay, which effectively says the latest large event dominates the older ones.
- For `15K-50K+` Mullin, `x = 0.65` is the best current practical default:
- materially better than `x = 0.00` on test and full-window returns
- better balanced than `x = 1.00`
- A `10`-slot queue still looks better than `20` for Mullin.
- Lower Mullin disclosure bands are less dangerous once decay and queue discipline are active, but the cleaner read still comes from focusing on the more meaningful signals.

## Most Useful Next Steps

- Sanity-check the Mullin event trace under `x = 0.65` so we understand whether the strong result is intuitive or just a side effect of near-total concentration in the latest event.
- Test whether the `50K+` Mullin high-decay plateau is robust to nearby implementation choices, or whether it is really just equivalent to “latest large event only.”
- Finish the narrowed `Ro Khanna` `50K+` queue-size / decay sweep once the first-time market cache warm completes.
- Run the same decay framework on `Josh Gottheimer`, `Kevin Hern`, and `David Taylor`, then compare their preferred decay levels against Mullin.
- Test whether queue length `8`, `10`, `12`, or `15` changes the Mullin result once decay is active, even though `20` already looks too loose.
