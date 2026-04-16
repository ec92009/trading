# Sandbox Strategy

This file describes the current sandbox strategy in plain English.

## What The Strategy Tries To Do

The idea is to hold a small basket of volatile assets, cut risk when one of them breaks down, and then rebalance back toward equal weights over time.

In practice, the simulator:

1. Starts with an equal-dollar allocation across the chosen assets.
2. Gives every asset its own stop floor and trailing trigger.
3. Sells part of a position when its price falls through the stop floor.
4. Moves stop-sale proceeds into a BTC buffer.
5. Rebalances the portfolio back toward equal weights at the end of each trading day.

The strategy is trying to do two things at once:

- protect against sharp down moves with partial stop sales
- keep buying back underweight assets through rebalancing

## Initial Portfolio Setup

On day one, the starting cash is split evenly across the selected symbols.

- Stocks are bought in whole shares only.
- BTC can be bought fractionally.
- If fractional-stock mode is enabled for an experiment, non-BTC assets can also be fractional.

Any leftover cash from the initial purchases can be swept into BTC if BTC is in the portfolio.

## How The Stop Works

Each asset has a stop floor based on its rolling beta versus SPY.

Higher-beta assets get wider stop distances.
Lower-beta assets get tighter stop distances.

The floor distance is:

- `base_tol × beta`
- with a minimum floor distance of `0.5%`

If the asset's daily low touches or breaks that floor, the simulator:

1. Sells a fraction of the position using `stop_sell_pct`.
2. Resets that asset's stop floor lower, based on the stop price and current beta.
3. Resets that asset's next trail trigger above the stop price.
4. Moves the proceeds into the BTC buffer or cash.

This is a partial stop, not a full exit.

## How The Trail Works

Each asset also has a trailing trigger above the current price.

If the asset closes above that trigger:

1. The stop floor is moved upward.
2. The next trail trigger is moved upward too.

This lets winners tighten their downside protection over time.

A trail update is not treated like a trade. It just updates the internal floor.

## What The BTC Buffer Does

BTC has two roles in the sandbox:

- BTC core: the normal BTC position in the portfolio
- BTC buffer: a temporary holding area for redeployment cash

When a stop sale happens in a non-BTC asset, the proceeds are usually parked in the BTC buffer first.

Later, if the rebalance logic needs cash to buy underweight assets, the simulator can sell BTC buffer back into cash to fund those purchases.

So the BTC buffer acts like a parking lot for capital between sells and rebuys.

## How Rebalancing Works

At the end of each trading day, after stop and trail checks, the simulator computes the total portfolio value and an equal-weight target for each asset.

Then it:

1. Sells overweight assets.
2. Buys underweight assets.
3. Uses the BTC buffer to help fund those buys when needed.

The goal is not to let one asset dominate forever and not to let a stopped asset stay permanently tiny if the portfolio can add back to it.

## One Trade Per Asset Per Day

The simulator currently limits each asset to one trade action per day.

That means:

- if an asset is stopped out that day, it cannot also be re-bought that same day
- if an asset is sold in rebalance, it cannot also be bought again that same day

BTC core and BTC buffer are treated as the same underlying asset for this rule.

This rule was added to stop unrealistic same-day churn.

## What Usually Happens In Practice

A common pattern looks like this:

1. The portfolio starts equal-weight.
2. A sharp down day triggers one or more partial stops.
3. The sold capital is parked in the BTC buffer.
4. On later days, the rebalance process buys back underweight names if they are eligible to trade and the portfolio has funding available.

So this is not a pure trend-following strategy and not a pure buy-and-hold strategy.

It is more like:

- equal-weight basket holding
- partial stop-loss de-risking
- gradual re-entry through daily rebalance

## Important Caveats

This is still a sandbox model, not a production trading system.

Important assumptions and simplifications include:

- daily bars only, not intraday execution
- no transaction fees
- no slippage
- stop decisions based on daily low
- rebalancing based on end-of-day values
- parameter optimization can overfit very easily

Because of that, very strong backtest results should be treated as hypotheses, not proof.
