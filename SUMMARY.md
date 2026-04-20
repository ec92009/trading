# Summary

This thread moved the repo from a partially manual Khanna paper-trading setup into a much more autonomous live posture.

## What changed

- Confirmed the `10K` bot is now the Ro Khanna daily copy-trade path through [bot_10k.py](/Users/ecohen/Dev/trading/bot_10k.py), not the old 5-name basket manager.
- Finished the daily market-data warm so the Khanna target book resolves from cache instead of depending on live Alpaca fetches during startup.
- Added persistent unsupported-symbol handling so Alpaca rejects like `7410Z`, `DE1`, and `SPX` are remembered and skipped automatically.
- Added autonomous Capitol Trades refresh in [khanna_daily/signal_updater.py](/Users/ecohen/Dev/trading/khanna_daily/signal_updater.py), with the Khanna bot checking on startup and every 15 minutes.
- Renamed the hidden `.cache` tree to visible [/_cache](/Users/ecohen/Dev/trading/_cache) and standardized it into:
- [/_cache/hourly_bars](/Users/ecohen/Dev/trading/_cache/hourly_bars)
- [/_cache/daily_bars](/Users/ecohen/Dev/trading/_cache/daily_bars)
- [/_cache/politicians](/Users/ecohen/Dev/trading/_cache/politicians)
- Added per-politician yearly signal caches under `/_cache/politicians/<politician_slug>/<YYYY>/signals.json`.
- Backfilled those yearly caches from the merged [copytrade_signals.json](/Users/ecohen/Dev/trading/copytrade_signals.json) file.
- Explicitly checked Mullin (`M001190`) after the yearly cache build and found no signals newer than the local merged dataset.

## Current live status

- The `10K` bot is running and healthy on the closed-market loop.
- It is autonomously checking Capitol Trades for Ro Khanna.
- Current Khanna refresh metadata is in [ro_khanna_refresh.json](/Users/ecohen/Dev/trading/_cache/politicians/ro_khanna_refresh.json).
- Current Mullin refresh metadata is in [markwayne_mullin_refresh.json](/Users/ecohen/Dev/trading/_cache/politicians/markwayne_mullin_refresh.json).
- Khanna and Mullin both have year-sliced signal caches under [/_cache/politicians](/Users/ecohen/Dev/trading/_cache/politicians).

## Operating direction

- The bot remains ordinary interpreted Python, not a compiled binary.
- The preferred deployment model is RSCP: robust Python service composition on an always-on machine or dedicated host, with pinned dependencies and a supervised process.
- The main open design question is whether the autonomous politician refresh framework should remain Khanna-only in the live bot or expand to more politicians now that yearly caches exist for the broader dataset.

## Where to look next

- [RESEARCH.md](/Users/ecohen/Dev/trading/RESEARCH.md) for the current live Capitol / Khanna deployment state
- [RESULTS.md](/Users/ecohen/Dev/trading/RESULTS.md) for research conclusions
- [STRATEGY.md](/Users/ecohen/Dev/trading/STRATEGY.md) for simulator mechanics
- [TODO.md](/Users/ecohen/Dev/trading/TODO.md) for follow-up decisions

## This session

- Upgraded the GitHub Pages viewer under [docs/](/Users/ecohen/Dev/trading/docs) into three dedicated tabs for Runtime Log, Decision Log, and Trade Journal.
- Added committed `10k` snapshot publishing for all three surfaces:
- [docs/data/recent_bot.log](/Users/ecohen/Dev/trading/docs/data/recent_bot.log)
- [docs/data/recent_decisions.json](/Users/ecohen/Dev/trading/docs/data/recent_decisions.json)
- [docs/data/recent_trades.tsv](/Users/ecohen/Dev/trading/docs/data/recent_trades.tsv)
- Added shared version publishing through [docs/data/version.json](/Users/ecohen/Dev/trading/docs/data/version.json) so the bot and web app stay on the same visible version.
- Bumped the shared version to `51.1` in [VERSION](/Users/ecohen/Dev/trading/VERSION), updated the docs / SOP notes, and restarted the live `10k` bot so new rationales use `BOT v51.1->...`.
- The local and LAN viewer URLs were verified at:
- [http://127.0.0.1:8011/](http://127.0.0.1:8011/)
- [http://192.168.1.155:8011/](http://192.168.1.155:8011/)
- The public viewer URL is [https://ec92009.github.io/trading/](https://ec92009.github.io/trading/) and may lag a minute or two behind the push while GitHub Pages refreshes.
- The Runtime Log tab was cleaned up to hide repeated `no new trades` polling and compact repeated closed-market / stale-order noise.
- The Decision Log cards were simplified to remove the order-payload section.
- The Trade Journal was compacted into a two-line mobile-friendly format:
- line 1: submitted/status/side/symbol/notional/rationale
- line 2: submitted/executed/filled
- The upload / drag-and-drop panel and extra intro block were removed from the viewer so the page focuses on the committed `10k` snapshots only.
