# trading Codex Review 2026-05-18

Reviewed: 2026-05-18 00:00 Europe/Madrid

1/ General architecture:
- The repo is a live-ops workspace with TeslaBot legacy code and CopyBot production code; preserving that split remains the right default.
- Strategy research, asset policy, Capitol refresh, order retry, and snapshot publishing need explicit profile boundaries to avoid cross-bot regressions.
- Because this checkout is the production source of truth, review-file work should stay isolated from behavior changes.

2/ UI:
- The docs dashboard is the visible UI, backed by profile-specific JSON/TSV snapshots.
- Startup diagnostics and dashboard labels should always state active bot profile, account, cache root, and snapshot destination.
- TeslaBot and CopyBot data should stay visually distinct to avoid operator mistakes.

3/ UX:
- Operator UX is the priority: safe startup, clear logs, no duplicate orders, obvious stale-data warnings, and clear retry limits.
- Cash injection/withdrawal and target-weight adjustments need a written SOP because operational mistakes have outsized impact.
- Automated repair/retry loops should report caps and final unresolved state.

4/ Testing:
- Existing tests cover strategy and repo audit areas.
- Add tests for incomplete-order retry caps, cash/position deltas, and profile-specific snapshot publication.
- Add startup diagnostic tests that assert CopyBot/TeslaBot paths do not cross.

5/ Everything else:
- A previous review existed and has been archived for this run.
- Do not rebase, force-push, or disrupt running services.
- Financial code changes should remain small, auditable, and accompanied by clear run/test notes.

6/ My suggetions:
1. Write a CopyBot cash injection/withdrawal SOP covering deposits, withdrawals, target weights, and reporting.
2. Add tests for incomplete-order retry caps and profile-specific snapshot publishing.
3. Refactor Capitol refresh toward a reusable multi-politician framework while keeping only Khanna live for execution.
4. Add startup diagnostics for active profile, account, cache roots, and snapshot destination.
5. Resolve the current dirty worktree in a focused commit before adding new trading behavior.
