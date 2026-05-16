# trading Codex Review 2026-05-15

1/ General architecture:
- The production-source-of-truth rule is critical: David's checkout is live ops, while GitHub is publication/history. Keep changes small and avoid structural refactors that disrupt running services.
- `bot.py`, `bot_10k.py`, `khanna_daily/live.py`, dashboards, caches, and viewer data need explicit boundaries. Introduce shared utilities only where they reduce duplication without moving production entry points.

2/ UI:
- The docs viewer/dashboard should present TeslaBot and CopyBot separately, with status, last signal refresh, last trade attempt, cache freshness, and recent errors.
- Avoid visual changes that obscure operational data density.

3/ UX:
- Operator UX matters more than aesthetic polish. Add clear runbooks for restart, stuck disclosure orders, cache refresh failures, and paper-account credential checks.
- Make incomplete disclosure-driven orders and retry state obvious in logs and viewer snapshots.

4/ Testing:
- Tests exist, including repo audit coverage. Expand around live-safe invariants: no duplicate buys on restart, TSV reconciliation, order retry idempotency, and cache fallback behavior.
- Add tests that protect the documented entry-point split so future refactors do not silently rename production paths.

5/ Everything else:
- The repo is currently dirty with bot and audit changes. Preserve and finish that work carefully before making backlog changes.
- Keep `_cache/`, logs, and trades TSV handling aligned with the AGENTS production rules.

6/ My suggetions:
1. Finish and verify the current bot/audit changes before starting new work.
2. Add tests for restart idempotency, retry state, TSV reconciliation, and cache fallback.
3. Add dashboard fields for signal freshness, retry status, and recent operational errors.
4. Write a short operator runbook for restart and failure recovery.
5. Keep production entry points stable unless David explicitly approves a refactor.
