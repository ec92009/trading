# Sandbox Strategy

This file describes the current sandbox strategy and the live profile it now feeds.

## Operating Assumptions

The current repo direction assumes:

- Alpaca remains the live broker path for the foreseeable future
- fractional stock trading is available and should be treated as the default live behavior
- simulator realism changes should bias toward Alpaca-compatible execution and settlement rules unless a comparison run says otherwise

## Current Default Basket

The basket is still:

- `TSLA`
- `TSM`
- `NVDA`
- `PLTR`
- `BTC/USD`

The current default target weights are:

- `TSLA`: `50%`
- `TSM`: `12.5%`
- `NVDA`: `12.5%`
- `PLTR`: `12.5%`
- `BTC/USD`: `12.5%`

These weights are the current live profile and the current simulator defaults. Historical benchmark results in [RESULTS.md](/Users/ecohen/Dev/trading/RESULTS.md) may refer to older equal-weight runs unless noted otherwise.

## What The Strategy Tries To Do

The idea is to hold a concentrated basket of volatile assets, de-risk quickly when one breaks down, and then rebuild exposure through a scheduled rebalance.

In practice, the simulator:

1. Starts with the configured target weights.
2. Gives every asset its own beta-scaled stop floor and trailing trigger.
3. Sells part of a position when its price falls through the stop floor.
4. Holds stop-sale and rebalance-sale proceeds in cash.
5. Rebalances the portfolio once per trading day, near the stock-market close.

So the strategy is trying to do two things at once:

- protect capital with partial stop sales and ratcheting floors
- keep capital deployed by buying underweight names back toward target weights

## Initial Portfolio Setup

At the start of a run, the initial cash is allocated by target weight.

- The default simulator path now uses fractional sizing for both stocks and crypto so entry and rebalance math better match the live bot.
- Whole-share stock mode is still available as an explicit comparison setting for research runs.
- Any leftover entry cash stays as cash until a later rebalance deploys it.

## How The Stop Works

Each asset has a stop floor based on its rolling beta versus `SPY`.

- higher-beta assets get wider stop distances
- lower-beta assets get tighter stop distances
- the effective floor distance is `max(0.5%, base_tol × beta)`

If the bar low touches or breaks that floor, the simulator:

1. Arms a stop sale for the next tradable bar.
2. Executes that sale at the worse of the breached floor and the next tradable bar open.
3. Resets that asset's stop floor from that realized stop anchor.
4. Resets that asset's next trail trigger above that realized stop anchor.
5. Sets a cooldown before that asset can stop-sell again.
6. Leaves the proceeds in cash for later redeployment.

This is a partial stop, not a full exit.

Important:

- the stop trigger still comes from hourly bar lows, not true tick data
- but the simulator no longer credits same-bar stop fills at the stop floor
- while a stop is armed, that symbol is blocked from rebalance trades until the stop executes

## How The Trail Works

Each asset also has a trailing trigger above the current price.

If price rises through that trigger:

1. The stop floor is moved upward.
2. The next trail trigger is moved upward too.

This lets winners tighten their downside protection over time.

Trail updates are not trades. They only revise internal risk levels.

## What The Cash Buffer Does

The sandbox now uses plain cash as the temporary holding area between sells and later rebalance buys.

Under the current Alpaca-aware realism model:

- crypto sale proceeds settle immediately and become usable cash on the same day
- stock sale proceeds move into unsettled cash and are only released on the next trading day

When a stop sale or rebalance sale happens, the proceeds stay in one of those cash buckets until a later rebalance decides where to redeploy them.

That keeps the strategy from taking extra crypto exposure just to park capital between trades.

## How Rebalancing Works

Rebalancing happens once per trading day on the last stock-session hourly bar.

The simulator computes total portfolio value and target dollar values from the configured target weights, then:

1. sells overweight positions
2. holds sale proceeds in cash
3. buys underweight positions from available cash

By default, stock trims and stock buys are fractional too, matching the live bot's notional-style stock behavior more closely.
The default simulator path also enforces a minimum rebalance gap and minimum order notional so tiny churn does not qualify as a trade.

The current default simulator path does not enforce a same-day re-entry guard, so a name can stop earlier in the day and still be bought back near the close if rebalance wants it.

## Stocks vs BTC Timing

The sandbox now uses an hourly stock-session clock plus BTC's 24x7 market:

- stocks use stock-session hourly bars
- any crypto symbol can still trigger stops and trail updates overnight and on weekends
- rebalance does not happen overnight or on non-trading days

The live bot is still more conservative than the simulator here:

- the live bot rebalances near the close on trading days
- the live bot now uses a cash buffer too
- the live bot now keeps `MANAGE_CRYPTO_24X7 = True`, so crypto risk monitoring keeps running off-hours

## Cooldown Semantics

Cooldown is in trading days, not calendar days.

After a stop on trading day `T`, the strategy skips stop-triggered sells for the configured number of trading sessions and only becomes stop-eligible again on the next allowed trading day.

This blocks repeated stop sells, but it does not freeze the floor forever:

- trail raises can still move the floor upward
- rebalance can still buy the asset back toward target

## Friction Model

The current simulator includes a first-pass Alpaca-aware friction model:

- stock slippage: `5 bps`
- BTC slippage: `10 bps`
- BTC taker fee: `25 bps`
- equity sell fees: SEC, TAF, and CAT pass-through fees

That model lives in [hourly_strategy.py](/Users/ecohen/Dev/trading/hourly_strategy.py).

## Data And Caching

The hourly simulator pulls stock and crypto bars from Alpaca.

- in-process caching avoids repeat downloads within one run
- disk caching now stores raw hourly bars under `.cache/hourly_data/`
- cache files are local-only and not committed

This makes reruns faster and keeps repeated research passes from asking Alpaca for the same data again and again.

## Important Caveats

This is still a sandbox model, not proof of live performance.

Important caveats include:

- stop decisions are still driven from hourly bar lows, not true tick-level execution
- stop execution is now next-tradable-bar and gap-aware, but it is still an hourly-bar approximation
- whole-share stock mode still exists for comparison work, so published results should note whether they used live-parity fractional stocks or comparison-mode whole shares
- parameter optimization can overfit very easily
- the live bot and simulator still differ in a few important behaviors
- the current production refit is in-sample and should not be treated like the validated 2023 / 9-quarter benchmark

Strong backtest results should still be treated as hypotheses, not proof.
