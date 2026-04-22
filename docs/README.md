# Trading Bot Viewer

Static GitHub Pages app for viewing committed CopyBot and TeslaBot logs in the browser.

The page now opens with a bot switcher plus four dedicated tabs:

- CopyBot
- TeslaBot

- Runtime Log
- Decision Log
- Trade Journal
- Last Portfolio

Recent viewer polish:

- `Last Portfolio` now shows `Asset / Target Weight / Current Weight / Points / Current Balance`
- the `Points` column now uses the simulator's actual current decayed point balances
- the Runtime Log compactor now collapses repeated closed-market snapshot churn and repeated `Waiting on N pending order(s)...` loops
- the Runtime Log `Show latest` control now counts visible UI cards after compaction, so the limit matches what the operator sees on screen
- the Trade Journal second line now reads in shorter operator-friendly timing, for example `Submitted ... / Executed in 3 s. / Filled immediately`

Each view reads the matching committed bundle from `docs/data/copybot/` or `docs/data/teslabot/`.

Committed snapshot bundles now live under `docs/data/copybot/` and `docs/data/teslabot/`.

CopyBot publishes fresh committed snapshots for:

- `recent_bot.log`
- `recent_decisions.json`
- `recent_trades.tsv`
- `recent_portfolio.json`
- `version.json`

TeslaBot can publish the same file set into its own bundle when remote snapshot publishing is enabled for the basket bot.
The CopyBot bundle is expected to reflect the real `10K` Khanna account even for direct-import snapshot jobs, not the smaller TeslaBot account.

Open `docs/index.html` locally for quick testing, or publish the `docs/` folder with GitHub Pages.

Published URL after GitHub Pages is enabled for `main` -> `/docs`:

- `https://ec92009.github.io/trading/`
