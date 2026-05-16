# trading Codex Review 2026-05-16

Review timestamp: 2026-05-16 02:03 CEST.

1/ General architecture:
- The repo now has a clearer split between TeslaBot, CopyBot, shared viewer snapshots, Capitol signal refresh, and simulation/research code.
- Because this checkout is the live operations source of truth, the highest architecture priority is preserving small, reversible changes and explicit runtime state boundaries.

2/ UI:
- The GitHub Pages viewer's CopyBot/TeslaBot switcher and four shared tabs are the right operator surface.
- Runtime logs should continue being compacted into operator-readable cards instead of raw process noise.

3/ UX:
- The bot naming cleanup reduces operational ambiguity.
- Cash injection/withdrawal, failed order recovery, and partial-fill handling need SOP-level workflows so future live actions do not depend on memory.

4/ Testing:
- The repo has focused tests for copytrade, hourly strategy, snapshots, audits, and weight shifts.
- Add regression tests for incomplete-order retry limits, profile binding, cash changes, and snapshot publishing for both bots.

5/ Everything else:
- There is unrelated local dirty live/research work in this checkout; preserve it and keep review-file changes separate.
- Logs, caches, and live state should remain protected from broad cleanup.

6/ My suggetions:
1. Write a CopyBot cash injection/withdrawal SOP covering deposits, withdrawals, target weights, and reporting.
2. Add tests for incomplete-order retry caps and profile-specific snapshot publishing.
3. Refactor Capitol refresh into a reusable multi-politician framework while keeping only Khanna live for execution.
4. Add explicit startup diagnostics showing active bot profile, account, cache roots, and snapshot destination.
5. Continue reducing broker-agnostic leftovers around the current Alpaca/fractional-stock operating model.
