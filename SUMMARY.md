Here’s the clean handoff for the next thread.

**Where We Landed**

We explored two tracks:

- a **CapitolTrades copy-trading idea** using Alpaca
- the existing **5-asset paper-trading sandbox** in this repo

The practical conclusion is that, for now, the best path is still the repo’s original sandbox strategy rather than the newer copy-trade or weight-shift variants.

**CapitolTrades Research**

We looked for one politician to follow and settled on **Sen. Markwayne Mullin** as the best default MVP target.

Why:
- CapitolTrades highlighted him in its **January 2, 2026** article as one of the politicians who “got it right” in 2025.
- His profile showed active trading into **2026**.
- As checked on **April 16, 2026**, his CapitolTrades profile showed roughly:
  - `501` trades
  - `39` filings
  - about `$24.25M` volume
  - last traded: **February 25, 2026**

Important caveat:
- CapitolTrades is **disclosure-driven**, not real-time.
- A copy bot would mirror **published disclosures**, not true same-day executions.

Recent Mullin filing behavior we reviewed:
- **Published January 16, 2026** for trades made **December 29, 2025**
  - large buys in names like `JPM`, `COF`, `GOOGL`, `MSFT`, `LRCX`, `AMZN`, `NVDA`
- **Published March 2, 2026** for trades made **February 4, 2026**
  - mixed rotation: buys like `MPWR`, `MCK`, `LRN`, `C`, `AMKR`; sells like `DELL`, `GS`, `MTZ`, `IRM`, `COHR`
- **Published March 10, 2026** for trades made **February 25, 2026**
  - `BUY UNH`
  - `SELL AZO`
  - `SELL INTU`

We also confirmed CapitolTrades usually gives:
- symbol
- buy/sell
- trade date
- publish date
- reporting lag
- ownership type
- size band like `15K-50K`, `50K-100K`, `100K-250K`
- sometimes price
- sometimes approximate share range

But it does **not** reliably provide exact size, so exact mirroring is not realistic.

**Copy-Trade Strategy Idea**

We simplified the copy-trade idea into:
- only copy **stock** trades
- only copy **larger size bands**
- use **published date** as signal date
- use **weight-based sizing**, not guessed exact dollar mirroring

We discussed:
- ignoring small trades
- using band thresholds like `50K-100K+`
- mapping trade bands into portfolio weights instead of fixed dollars

I built a small demo:
- [copytrade_demo.py](/Users/ecohen/Dev/trading/copytrade_demo.py)
- [copytrade_signals.json](/Users/ecohen/Dev/trading/copytrade_signals.json)

That demo showed the mechanics, but it was only a recent-signal proof of concept, not a full historical strategy.

**Current Core Sandbox Strategy**

The repo’s main sandbox strategy is still the one in [sim.py](/Users/ecohen/Dev/trading/sim.py):

- hold a 5-name basket
- start equal-weight
- use **beta-scaled stop floors**
- use **trailing floor raises**
- if a name hits its stop:
  - sell a fixed fraction of the position
  - park proceeds into the `BTC Buffer`
- rebalance at end of day
- no extra weight shifting

In plain terms:
- equal-weight basket
- partial de-risking on weakness
- gradual re-entry through rebalance
- BTC acts as both a core asset and a temporary buffer

We also clarified one behavior:
- if a stock hits its stop, it is sold that day and the proceeds are parked
- that exact stock is generally **not** bought back the same day because of the one-trade-per-asset-per-day rule
- but it can be bought back on the **next trading day** or later during rebalance

So the current pattern is basically:
- sell today
- park in BTC
- possibly re-enter tomorrow or later

**Weight-Shifting Experiment**

You proposed a new variant:

- when a stock hits its stop, reduce its target weight by `X%` and redistribute that weight equally across the other assets
- when a stock clears its upper bound / trail trigger, increase its target weight by `Y%`
- no immediate trade at the trigger moment
- only rebalance at end of day
- optimize `X` and `Y`

I implemented that as a separate sandbox:
- [weight_shift_strategy.py](/Users/ecohen/Dev/trading/weight_shift_strategy.py)
- [optimize_weight_shift.py](/Users/ecohen/Dev/trading/optimize_weight_shift.py)
- [tests/test_weight_shift_strategy.py](/Users/ecohen/Dev/trading/tests/test_weight_shift_strategy.py)

**Weight-Shifting Results**

We tested on the usual 5-name basket:
- `TSLA`
- `TSM`
- `NVDA`
- `PLTR`
- `BTC-USD`

Train/test split:
- **Train:** 2023
- **Test:** 2024-01-02 through **2026-03-31**

Whole-share run:
- best train result came from roughly `X=0%, Y=5%`
- but on holdout, that underperformed the plain baseline
- baseline `X=0, Y=0` was better

Fractional-stock / Alpaca-style run:
- same conclusion
- `X=0%, Y=5%` won training
- but still lost to baseline on holdout

Bottom line:
- **weight shifting did not help**
- not in whole-share mode
- not in fractional-stock mode

So we explicitly concluded:
- drop the weight-shifting branch
- keep the simpler baseline

**Beta Scaling Check**

You asked whether the existing **beta scaling** really helps.

I ran the baseline strategy two ways:
- current beta-scaled logic
- same strategy with beta forced to `1.0` everywhere

Results:

2023 train:
- beta-scaled: **$25,754.14**, **+157.54%**, **15.78% max DD**
- no-beta: **$25,293.92**, **+152.94%**, **16.08% max DD**

2024-01-02 to 2026-03-31 holdout:
- beta-scaled: **$34,844.29**, **+248.44%**, **30.70% max DD**
- no-beta: **$35,406.99**, **+254.07%**, **29.41% max DD**

Interpretation:
- beta scaling helped on the training window
- it did **not** beat the no-beta version on the holdout
- but it made the system much calmer and less churny

Holdout trade counts:
- beta-scaled:
  - stops `303`
  - trails `189`
- no-beta:
  - stops `591`
  - trails `321`

We decided to **keep beta scaling** anyway because it’s the more controlled version and the holdout edge for no-beta was not large enough to justify the extra churn.

**Broker / Execution Notes**

We clarified broker capabilities relevant to the project:

- `Schwab`
  - fractional shares: yes, but limited / product-specific
  - direct spot BTC trading: no
  - BTC ETF exposure: yes
- `Robinhood`
  - fractional shares: yes
  - BTC trading: yes in general, though your setup/workflow wasn’t the target here
- `Alpaca`
  - fractional shares: yes
  - direct `BTC/USD` trading: yes
  - paper and live environments both exist

We agreed that for this repo and this strategy, **Alpaca is the cleanest fit**.

**Current Best Strategy**

Best practical strategy after all of this:

- keep the original **5-name equal-weight basket**
- keep **beta-scaled stops**
- keep **trailing floor raises**
- keep **partial stop sales**
- keep **BTC buffer**
- keep **end-of-day rebalance**
- **do not** add weight shifting
- **do not** switch away from beta scaling right now

That’s the best summary of where we ended.

**Files Added During This Thread**

Copy-trade demo:
- [copytrade_demo.py](/Users/ecohen/Dev/trading/copytrade_demo.py)
- [copytrade_signals.json](/Users/ecohen/Dev/trading/copytrade_signals.json)
- [tests/test_copytrade_demo.py](/Users/ecohen/Dev/trading/tests/test_copytrade_demo.py)

Weight-shift sandbox:
- [weight_shift_strategy.py](/Users/ecohen/Dev/trading/weight_shift_strategy.py)
- [optimize_weight_shift.py](/Users/ecohen/Dev/trading/optimize_weight_shift.py)
- [weight_shift_optimizer_results.json](/Users/ecohen/Dev/trading/weight_shift_optimizer_results.json)
- [weight_shift_optimizer_results_fractional.json](/Users/ecohen/Dev/trading/weight_shift_optimizer_results_fractional.json)
- [tests/test_weight_shift_strategy.py](/Users/ecohen/Dev/trading/tests/test_weight_shift_strategy.py)

**Most Important One-Line Takeaway**

The original baseline strategy still wins:
equal-weight 5-name basket, beta-scaled stops, trailing floors, partial sells into BTC buffer, end-of-day rebalance, no extra weight shifting.
