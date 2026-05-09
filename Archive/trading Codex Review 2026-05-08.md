# trading Codex Review 2026-05-08

Generated: 2026-05-08 00:00 Europe/Madrid

1/ General architecture

- trading is live-ops sensitive. Preserve the documented split: TeslaBot legacy path, CopyBot production path through `bot_10k.py` and `khanna_daily/live.py`, and viewer data under `docs/data/...`.
- The repo mixes live service code, research scripts, optimizers, generated JSON results, dashboards, and docs. The next architecture gain is stronger separation between production runtime, research/backtest tooling, generated artifacts, and public dashboard publishing.

2/ UI

- The dashboard/public viewer should foreground bot status, latest decision, portfolio, pending orders, data freshness, and warning states.
- Make TeslaBot versus CopyBot visually unmistakable so the legacy bot and current production bot cannot be confused.

3/ UX

- Operational UX matters more than feature expansion. A maintainer needs to answer: is the service running, is data fresh, what did it decide, what orders are pending, what failed, and what should I do now?
- Add explicit "do not auto-promote" language in any optimizer output or dashboard area that could be mistaken for a production recommendation.

4/ Testing

- There are several tests, including repo-audit and strategy tests. Expand around CopyBot signal refresh, cache layout migration, incomplete disclosure retries, order idempotency, and dashboard snapshot generation.
- Add tests that guard against accidental path refactors for `bot_10k.py`, `khanna_daily/live.py`, `_cache/`, and `docs/data/copybot`.

5/ Everything else

- The worktree is already dirty with user changes in docs, signals, tests, and `khanna_daily/signal_updater.py`; any next development pass should inspect those before editing.
- Because this touches trading, every behavior change should be small, reviewable, and simulation-backed before live service use.

6/ My suggetions:

1. Add regression tests for CopyBot signal refresh, cache layout, incomplete-order retry, and idempotent order logging.
2. Separate production runtime modules from research/optimizer scripts and generated result artifacts.
3. Improve dashboard freshness/status panels for service health, latest decision, pending orders, and failure states.
4. Add path-contract tests protecting the documented production file and snapshot layout.
5. Keep optimizer outputs clearly labeled as research and not auto-promoted production settings.
