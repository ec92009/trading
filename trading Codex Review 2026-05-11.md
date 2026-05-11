# trading Codex Review 2026-05-11

Review time: 2026-05-11 02:05 CEST.

1/ General architecture

- The repo has a clear production warning: David's always-on Mac checkout is the live source of truth, and GitHub is publication/history. That constraint should shape every change.
- The TeslaBot/CopyBot split is documented and should stay intact unless explicitly approved.
- Live bot code, research scripts, dashboards, caches, logs, and docs live close together. The next architectural cleanup should be additive and low-risk, not a sweeping rename.

2/ UI

- The visible UI appears to be the dashboard and GitHub Pages viewer under `docs/`.
- Operational views should separate live state, historical simulation, and research output so a user does not confuse backtest results with current bot action.
- Version/status badges should make it obvious which bot and account path a page describes.

3/ UX

- For a live paper-trading system, operator UX matters: current holdings, pending orders, last signal refresh, last successful snapshot, last broker error, and whether markets are open.
- Failure states should be actionable and conservative. Missing credentials, stale Capitol data, Alpaca errors, and incomplete disclosure-driven orders need distinct messages.
- The "do-not-auto-promote" policy around refit results is important and should stay visible near any optimizer output.

4/ Testing

- Five tests were detected, mostly around CopyBot/demo paths.
- Add broker-adapter tests with mocked Alpaca responses for rejected orders, partial fills, retry behavior, and market-closed handling.
- Add snapshot/viewer tests that validate `docs/data/copybot` and `docs/data/teslabot` schemas before publishing.

5/ Everything else

- `.env`, logs, JSONL decisions, cache, and state files appear in the tree scan. Confirm sensitive/runtime artifacts are ignored and not committed.
- Any future automation should preserve running services and local operational files exactly as AGENTS.md requires.

6/ My suggetions:

1. Add mocked Alpaca tests for CopyBot order retries, partial fills, market-closed behavior, and broker errors.
2. Add schema validation for published viewer snapshots under `docs/data`.
3. Add an operator status page section for last signal refresh, last broker sync, pending orders, and stale-data warnings.
4. Audit ignored/runtime files to ensure logs, state, caches, and credentials cannot be committed accidentally.
5. Keep optimizer/refit work clearly labeled as research unless manually promoted.
