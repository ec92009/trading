# Trading Log Viewer

Static GitHub Pages app for viewing local trading logs in the browser.

The page now opens with three dedicated tabs:

- Runtime Log
- Decision Log
- Trade Journal

Each tab reads the matching committed snapshot from `docs/data/` by default.

The running `10k` bot publishes fresh committed snapshots for:

- `recent_bot.log`
- `recent_decisions.json`
- `recent_trades.tsv`
- `version.json`

Open `docs/index.html` locally for quick testing, or publish the `docs/` folder with GitHub Pages.

Published URL after GitHub Pages is enabled for `main` -> `/docs`:

- `https://ec92009.github.io/trading/`
