# trading Codex Review 2026-05-10

Timestamp: 2026-05-10 02:04 CEST

1/ General architecture:

- The repo contains live production bots, research scripts, optimizer artifacts, dashboard publishing, and tests. The most important architectural boundary is production runtime versus research/backtest tooling.
- The `AGENTS.md` production-source-of-truth warning is essential. Preserve the current entrypoint split while gradually isolating CopyBot service code from exploratory optimization scripts.

2/ UI:

- The dashboard and static viewer should emphasize operational state: latest heartbeat, latest signal refresh, pending orders, last successful snapshot, stale data warnings, and latest error.
- Viewer data under `docs/data/copybot` and `docs/data/teslabot` should make freshness visibly obvious to avoid stale-public-page confusion.

3/ UX:

- Operator workflows should be explicit: check service health, inspect pending orders, reconcile fills, refresh signals, publish viewer snapshot, and recover from Alpaca/API failure.
- Add commands that summarize "is it safe to leave running?" rather than requiring manual inspection of several logs and JSON files.

4/ Testing:

- Tests exist and should expand around the production CopyBot path: signal refresh, cache layout, retry/idempotency, order reconciliation, and snapshot generation.
- Add contract tests that assert production paths (`bot.py`, `bot_10k.py`, `docs/data/...`, `_cache/...`) remain stable.

5/ Everything else:

- Research artifacts need labels that say whether they are historical, current experiment, or approved production settings.
- Because this is live-ops-adjacent even with paper trading, prefer small changes with explicit rollback notes.

6/ My suggetions:

1. Add regression tests for CopyBot signal refresh, cache layout, retry/idempotency, and snapshot generation.
2. Split production runtime, research/backtest scripts, generated artifacts, and dashboard publishing more clearly.
3. Improve viewer freshness/status panels for service health, latest decision, pending orders, and failures.
4. Add path-contract tests for production entrypoints and snapshot directories.
5. Label optimizer outputs as research and require explicit promotion into production settings.
