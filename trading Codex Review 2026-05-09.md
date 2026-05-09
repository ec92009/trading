# trading Codex Review 2026-05-09

Generated: 2026-05-09 00:00 Europe/Madrid

1/ General architecture

- trading is live-ops sensitive. Preserve the documented split: TeslaBot legacy path, CopyBot production path through `bot_10k.py` / `khanna_daily/live.py`, and public viewer snapshots under `docs/data/...`.
- The repo mixes live runtime, research scripts, optimizers, generated results, dashboards, and docs. Separate production runtime modules from research/backtest tooling and generated artifacts more explicitly.

2/ UI

- The viewer should foreground bot status, latest decision, pending orders, portfolio freshness, data freshness, and warning states.
- TeslaBot versus CopyBot must remain visually unmistakable so legacy and production account contexts cannot be confused.

3/ UX

- Operator UX matters most: is the service running, is data fresh, what did it decide, what orders are pending, what failed, and what should happen next?
- Optimizer and research outputs should never look like auto-promoted production recommendations. Label them as research and require explicit promotion steps.

4/ Testing

- The repo has meaningful tests, including audit and strategy tests. Expand around CopyBot signal refresh, cache layout, incomplete disclosure retries, order idempotency, and snapshot generation.
- Add path-contract tests protecting `bot_10k.py`, `khanna_daily/live.py`, `_cache/`, and `docs/data/copybot` assumptions.

5/ Everything else

- Because this touches trading behavior, every behavior change should stay small, reviewable, and simulation-backed before live service use.
- Keep generated/live snapshot churn distinct from code changes so operational commits remain easy to review.

6/ My suggetions:

1. Add regression tests for CopyBot signal refresh, cache layout, retry/idempotency, and snapshot generation.
2. Split production runtime, research/backtest scripts, generated artifacts, and dashboard publishing more clearly.
3. Improve viewer freshness/status panels for service health, latest decision, pending orders, and failures.
4. Add path-contract tests for production entrypoints and snapshot directories.
5. Label optimizer outputs as research and require explicit promotion into production settings.
