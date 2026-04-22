# Summary

This thread moved the repo from a partially manual Khanna paper-trading setup into a much more autonomous copy-bot posture.

## What changed

- Confirmed CopyBot is now the Ro Khanna daily copy-trade path through [bot_10k.py](/Users/ecohen/Dev/trading/bot_10k.py), while the old 5-name basket manager in [bot.py](/Users/ecohen/Dev/trading/bot.py) is now TeslaBot.
- Finished the daily market-data warm so the Khanna target book resolves from cache instead of depending on live Alpaca fetches during startup.
- Added persistent unsupported-symbol handling so Alpaca rejects like `7410Z`, `DE1`, and `SPX` are remembered and skipped automatically.
- Added autonomous Capitol Trades refresh in [khanna_daily/signal_updater.py](/Users/ecohen/Dev/trading/khanna_daily/signal_updater.py), with CopyBot checking on startup and every 15 minutes.
- Renamed the hidden `.cache` tree to visible [/_cache](/Users/ecohen/Dev/trading/_cache) and standardized it into:
- [/_cache/hourly_bars](/Users/ecohen/Dev/trading/_cache/hourly_bars)
- [/_cache/daily_bars](/Users/ecohen/Dev/trading/_cache/daily_bars)
- [/_cache/politicians](/Users/ecohen/Dev/trading/_cache/politicians)
- Added per-politician yearly signal caches under `/_cache/politicians/<politician_slug>/<YYYY>/signals.json`.
- Backfilled those yearly caches from the merged [copytrade_signals.json](/Users/ecohen/Dev/trading/copytrade_signals.json) file.
- Explicitly checked Mullin (`M001190`) after the yearly cache build and found no signals newer than the local merged dataset.

## Current live status

- CopyBot is running and healthy on the closed-market loop.
- It is autonomously checking Capitol Trades for Ro Khanna.
- Current Khanna refresh metadata is in [ro_khanna_refresh.json](/Users/ecohen/Dev/trading/_cache/politicians/ro_khanna_refresh.json).
- Current Mullin refresh metadata is in [markwayne_mullin_refresh.json](/Users/ecohen/Dev/trading/_cache/politicians/markwayne_mullin_refresh.json).
- Khanna and Mullin both have year-sliced signal caches under [/_cache/politicians](/Users/ecohen/Dev/trading/_cache/politicians).

## Operating direction

- The bot remains ordinary interpreted Python, not a compiled binary.
- The preferred deployment model is RSCP: robust Python service composition on an always-on machine or dedicated host, with pinned dependencies and a supervised process.
- The main open design question is whether the autonomous politician refresh framework should remain Khanna-only in CopyBot or expand to more politicians now that yearly caches exist for the broader dataset.

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
- Bumped the shared version through `51.3` in [VERSION](/Users/ecohen/Dev/trading/VERSION), kept [docs/data/version.json](/Users/ecohen/Dev/trading/docs/data/version.json) aligned, and reloaded CopyBot so new rationales use the same shared version source.
- The local and LAN viewer URLs were verified at:
- [http://127.0.0.1:8011/](http://127.0.0.1:8011/)
- [http://192.168.1.191:8011/](http://192.168.1.191:8011/)
- The public viewer URL is [https://ec92009.github.io/trading/](https://ec92009.github.io/trading/) and may lag a minute or two behind the push while GitHub Pages refreshes.
- The Runtime Log tab was cleaned up to hide repeated `no new trades` polling and compact repeated closed-market / stale-order noise.
- The Runtime Log compactor was refined again so a `signal changed while market was closed` line and the following `Market closed. Next open ...` line collapse into a single combined entry before repeated pairs are grouped.
- The Decision Log cards were simplified to remove the order-payload section.
- The Trade Journal was compacted into a two-line mobile-friendly format:
- line 1: submitted/status/side/symbol/notional/rationale
- line 2: submitted/executed/filled
- The upload / drag-and-drop panel and extra intro block were removed from the viewer so the page focuses on the committed copy-bot snapshots only.
- The filter bar was simplified to a single full-text search, `Show latest`, and an explicit `Apply Filters` button, with Enter-to-apply support.
- The repo was synced forward to the latest `origin/main` first, with local research changes preserved across the fast-forward.
- Added a fourth `Last Portfolio` tab to the viewer, backed by committed portfolio snapshots in [docs/data/recent_portfolio.json](/Users/ecohen/Dev/trading/docs/data/recent_portfolio.json).
- Added shared snapshot publishing support for the portfolio view in [remote_snapshots.py](/Users/ecohen/Dev/trading/remote_snapshots.py).
- Fixed the trade journal / order sync path so partial fills are preserved as `partial_fill_canceled` with `filled_qty`, instead of lingering as `pending` or collapsing into plain `canceled`.
- Fixed the Khanna completion logic so, when a disclosure-driven rebalance is underfilled, later open-market heartbeat cycles only retry the incomplete symbols rather than running a fresh full rebalance.
- Capped incomplete-order retries at `5` attempts per asset, and made retry rationales explicit with the current bot version and attempt number.
- Fixed a rationale-matching bug where older incomplete `v50.0` Khanna orders were being ignored after the bot version advanced, which had left excess cash stranded in the copy-bot paper account.
- Verified the live fix against the real copy-bot account: the patched completion path immediately submitted catch-up buys for `KO`, `VIG`, `AMZN`, `ACI`, `TD`, and `AMRZ`, reducing idle cash.
- Tightened the Runtime Log viewer so `ORDER SYNC ...` entries render as simplified operator-readable lines instead of raw IDs and plumbing details.
- Restored a dedicated asset filter as a dropdown so symbol filtering is field-based rather than broad text search.
- Reworked the `Last Portfolio` columns to `Asset / Target Weight / Current Weight / Points / Current Balance`, using the active simulation state to expose the current point distribution.
- Bumped the shared bot/web version to `51.4`, cache-busted the local viewer assets in [docs/index.html](/Users/ecohen/Dev/trading/docs/index.html), refreshed [docs/data/version.json](/Users/ecohen/Dev/trading/docs/data/version.json), and restarted CopyBot on the patched code.
- Corrected the `Last Portfolio` `Points` column so it now shows the simulator's actual current decayed point balances instead of a weight-derived stand-in.
- Tightened the Runtime Log compactor again so long overnight closed-market stretches collapse into a single session-style summary, and repeated `Waiting on N pending order(s)...` loops also collapse into one operator-readable card.
- Refined the Trade Journal second line to plain elapsed timing like `Submitted ... / Executed 3 seconds later / Filled 0 seconds later`.
- Bumped the shared bot/web version again to `51.5` so the local and published viewer pick up the latest cache-busted assets for this polish cycle.
- Added a repo-level `Show Me` SOP in [AGENTS.md](/Users/ecohen/Dev/trading/AGENTS.md) so future viewer requests default to running the local `docs/` server, pushing committed `main` for GitHub Pages when asked, and reporting localhost / LAN / public URLs plus the visible version.
- Synced the repo to the latest GitHub `main`, preserved the unrelated local research work, and pushed the committed viewer state so the public site stayed current.
- Refined the Trade Journal timing line again so the operator sees shorter phrasing like `Executed in 1 s.` and `Filled immediately`.
- Changed the Runtime Log `Show latest` control to count visible compacted UI cards instead of raw log lines, and made the label explicit on that tab as `Show latest UI entries`.
- Refreshed the viewer docs in [README.md](/Users/ecohen/Dev/trading/README.md) and [docs/README.md](/Users/ecohen/Dev/trading/docs/README.md) to match the current four-tab `docs/` viewer behavior.
- Bumped the shared bot/web version to `53.0`, refreshed the cache-busted viewer assets in [docs/index.html](/Users/ecohen/Dev/trading/docs/index.html), kept [docs/data/version.json](/Users/ecohen/Dev/trading/docs/data/version.json) aligned, and pushed `main` so GitHub Pages can publish the update.
- Split the inline environment and web-viewer workflow guidance out of [AGENTS.md](/Users/ecohen/Dev/trading/AGENTS.md) into [ENVIRONMENT_SOP.md](/Users/ecohen/Dev/trading/ENVIRONMENT_SOP.md) and [SHOW_ME_SOP.md](/Users/ecohen/Dev/trading/SHOW_ME_SOP.md), leaving `AGENTS.md` as a pointer file like the existing research-context section.
- Updated the workspace install preference from `pip` to `uv`, including the setup snippet in [README.md](/Users/ecohen/Dev/trading/README.md) and the package-management guidance in [ENVIRONMENT_SOP.md](/Users/ecohen/Dev/trading/ENVIRONMENT_SOP.md).
- Refreshed the root doc index in [README.md](/Users/ecohen/Dev/trading/README.md) so the new SOP files are listed alongside the research and strategy docs.

## Latest Session

- Standardized the repo naming so the old ~$350 basket account is documented as `TeslaBot` and the ~$10K Ro Khanna account is documented as `CopyBot`.
- Refreshed the Markdown docs to distinguish TeslaBot from CopyBot instead of calling both “the live bot” or “the 10K bot”.
- Added a top-level comparison table in [README.md](/Users/ecohen/Dev/trading/README.md) covering entrypoints, accounts, roles, cadence, and the new `.env` variable names.
- Updated the documented credential names to `TESLABOT_API_KEY`, `TESLABOT_SECRET_KEY`, `TESLABOT_BASE_URL`, `COPYBOT_API_KEY`, `COPYBOT_SECRET_KEY`, and `COPYBOT_BASE_URL`.
- Updated [alpaca_env.py](/Users/ecohen/Dev/trading/alpaca_env.py) so TeslaBot reads `TESLABOT_*` first and CopyBot reads `COPYBOT_*` first, with the older `ALPACA_*` names retained as fallback compatibility.
- Updated [dashboard.py](/Users/ecohen/Dev/trading/dashboard.py) so the TeslaBot dashboard reads and writes `TESLABOT_*` env settings.
- Fixed the stale `ALPACA__10K_BASE_URL` typo while touching the shared Alpaca credential loader.
- Installed `pytest` into the repo venv with `uv` and ran [tests/test_repo_audit.py](/Users/ecohen/Dev/trading/tests/test_repo_audit.py) successfully: `37 passed` with one dependency deprecation warning from `websockets.legacy`.
- Restarted both launchd services and verified they were healthy afterward:
- `com.trading.bot` for TeslaBot
- `com.trading.bot.10k` for CopyBot
