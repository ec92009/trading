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

## Capitol Weighting Rules

- We dropped the old `literal` copy mode.
- Active names now use a point-based target system:
- `50K+` disclosure bands: `4` points
- `15K-50K`: `2` points
- `1K-15K`: `1` point
- Target weight is `symbol points / total active points`.

## Capitol Queue Rules

- The current Mullin simulator now applies a capped exit queue.
- Default queue limit is `10` names.
- Queue behavior:
- FIFO within each band
- lower bands sit closer to the exit
- higher bands sit farther from the exit
- when a new name causes overflow, eviction happens from the front of the queue
- Practical implication:
- weak bands can enter, but they are first to be crowded out
- within the same band, older names exit before newer ones

## Latest Mullin Results

All results below use fresh `$10,000` cash in each window, daily policy, next-trading-day execution, fractional shares, and the `10`-name queue cap.

### Out-of-sample test (`2026-01-01` to `2026-04-19`)

| Threshold | Mullin Return | SPY Return | Transactions | Queue Evictions | Final Names |
|---|---:|---:|---:|---:|---:|
| `50K-100K+` | `+5.74%` | `+3.76%` | `21` | `1` | `10` |
| `15K-50K+` | `+5.79%` | `+3.76%` | `41` | `39` | `10` |
| `1K-15K+` | `+5.79%` | `+3.76%` | `41` | `40` | `10` |

Interpretation:

- The `10`-name cap fixed the worst portfolio bloat from lower-threshold Mullin runs.
- But the smaller disclosures still did not add much value:
- test return only improved from `+5.74%` to `+5.79%`
- transactions nearly doubled from `21` to `41`
- most smaller names churned through the queue and did not survive into the final top `10`

### In-sample train (`2025-08-13` to `2025-12-31`)

| Threshold | Mullin Return | SPY Return | Transactions | Final Names |
|---|---:|---:|---:|---:|
| `50K-100K+` | `+64.47%` | `+6.21%` | `10` | `4` |
| `15K-50K+` | `+49.63%` | `+6.21%` | `17` | `7` |
| `1K-15K+` | `+46.60%` | `+6.21%` | `20` | `8` |

Interpretation:

- Train performance is strong but comes from a very short and sparse actionable history.
- The out-of-sample test read matters much more than the train result.

## Current Conclusion

- For the main 5-name basket, buy-and-hold and rebalance-only remain the only credible live postures under the corrected simulator.
- For Capitol, Mullin still looks promising as a standalone normalized strategy, but the edge is modest and the actionable history is short.
- The current best Capitol formulation is still the higher-threshold version:
- simpler
- fewer names
- lower churn
- nearly identical out-of-sample return to lower-threshold variants
- Lower Mullin disclosure bands are now less dangerous because of the `10`-name queue cap, but they still look more like extra activity than extra edge.

## Most Useful Next Steps

- Add explicit stale-signal expiry to the Mullin queue so old names do not persist indefinitely without reinforcement.
- Test whether queue length `10` is actually best, or whether `8`, `12`, or `15` changes the churn/return balance.
- Add band-upgrade and refresh rules so repeat disclosures can improve queue position instead of only entering once.
- Compare Mullin against at least one additional Capitol source, especially Josh Gottheimer, using the same daily-policy simulator.
