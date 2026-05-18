# trading Codex Review 2026-05-17

Reviewed: 2026-05-17 02:04

1/ General architecture:
- The repo is a live-ops workspace with TeslaBot legacy code and CopyBot production code; preserving that split is correctly documented.
- Recent local changes touch live trading files, tests, and policy code, so review/commit boundaries should be kept very tight.
- Capitol refresh, order retry, asset policy, and snapshot publishing need explicit profile boundaries to avoid cross-bot regressions.

2/ UI:
- The docs dashboard is the visible UI, backed by profile-specific JSON/TSV snapshots.
- Startup diagnostics and dashboard labels should always state active bot profile, account, cache root, and snapshot destination.
- The viewer should keep TeslaBot and CopyBot data visually distinct.

3/ UX:
- Operator UX is the priority: safe startup, clear logs, no duplicate orders, and obvious stale-data warnings.
- Cash injection/withdrawal and target-weight adjustments need a written SOP because operational mistakes have outsized impact.
- Any automated repair/retry loop should report caps and final unresolved state.

4/ Testing:
- Existing tests cover strategy and repo audit areas.
- Add tests for incomplete-order retry caps, cash/position deltas, and profile-specific snapshot publication.
- Add startup diagnostic tests that assert CopyBot/TeslaBot paths do not cross.

5/ Everything else:
- The repo has pre-existing uncommitted work and is ahead of origin; because it is production source of truth, do not mix unrelated changes.
- Do not rebase, force-push, or disrupt running services.
- Financial code changes should prefer small, auditable commits with clear run/test notes.

6/ My suggetions:
1. Write a CopyBot cash injection/withdrawal SOP covering deposits, withdrawals, target weights, and reporting.
2. Add tests for incomplete-order retry caps and profile-specific snapshot publishing.
3. Refactor Capitol refresh toward a reusable multi-politician framework while keeping only Khanna live for execution.
4. Add explicit startup diagnostics for active profile, account, cache roots, and snapshot destination.
5. Resolve the current dirty worktree in a focused commit before adding new trading behavior.
