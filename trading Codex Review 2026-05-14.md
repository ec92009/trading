# trading Codex Review 2026-05-14

Review timestamp: 2026-05-14, Europe/Madrid.

1/ General architecture

- The repo now has a clear operational split: TeslaBot remains the old small basket bot, CopyBot is the current Ro Khanna copy-trade bot, and GitHub Pages exposes committed viewer snapshots.
- The live checkout is explicitly the production source of truth, so changes must be small, testable, and coordinated with running launchd services.
- There is existing dirty work in live bot files and tests; do not mix review artifact commits with production bot edits.

2/ UI

- The dual-bot viewer direction is useful, with Runtime Log, Decision Log, Trade Journal, and Last Portfolio tabs.
- Operator-facing log compaction is important; keep simplifying repetitive market-closed/order-sync noise.
- The CopyBot/TeslaBot switcher should always make account scale and data freshness obvious.

3/ UX

- The biggest UX risk is operational ambiguity: which bot is live, which account snapshot is shown, and whether a viewer value is fresh.
- Retry and incomplete-order behavior has improved; keep rationales explicit and versioned.
- Real-money Robinhood exploration belongs behind a separate risk/SOP track, not inside the current paper-bot loop.

4/ Testing

- Existing tests cover strategy, snapshots, copytrade demo, and repo audit; keep expanding around production guardrails.
- Add tests for snapshot identity/account binding, unsupported-symbol persistence, incomplete-order retry limits, and environment variable precedence.
- Add a viewer smoke test for both CopyBot and TeslaBot snapshot bundles.

5/ Everything else

- `bot_10k.log` is large runtime output; make sure logs stay ignored and are not accidentally committed.
- The repo has active uncommitted production changes; review-only work should not stage those files.
- Keep `_cache/`, `trades*.tsv`, live logs, and launchd services protected.

6/ My suggetions:

1. Finish or isolate the current dirty production changes before starting new bot behavior work.
2. Add snapshot-account binding tests so CopyBot cannot publish TeslaBot account data again.
3. Add regression coverage for unsupported symbols and incomplete-order retry limits.
4. Add a viewer smoke test that loads both bot bundles and checks freshness/version fields.
5. Create a cash injection/withdrawal SOP for CopyBot.
6. Keep real-money broker research in a separate risk-controlled document and do not wire it into live code yet.
