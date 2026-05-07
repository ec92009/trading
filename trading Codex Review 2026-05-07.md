# trading Codex Review 2026-05-07

Reviewed at: 2026-05-07 00:00 Europe/Madrid

1/ General architecture:
- The repo contains live bot code, simulator/research code, dashboard code, and operational artifacts. Preserve the current production split, but keep extracting shared trading primitives carefully: broker access, order reconciliation, signal snapshots, portfolio math, and reporting.
- Treat CopyBot as the primary production path and TeslaBot as legacy unless a specific task says otherwise.
- Avoid broad refactors around `bot.py`, `bot_10k.py`, and `khanna_daily/live.py`; live ops safety is more important than aesthetic cleanup.

2/ UI:
- `dashboard.py` and `docs/app.js` are large enough to justify separating data loading, presentation, and control/actions.
- The viewer should clearly label TeslaBot versus CopyBot, paper versus real-money assumptions, data freshness, and last successful snapshot.
- Add stronger empty/error states for missing local logs, stale `_cache`, and unavailable Alpaca/Capitol data.

3/ UX:
- Operational UX should focus on safe observability: what is running, what it last decided, what orders are pending/filled, and whether it is safe to restart.
- Add explicit cash injection/withdrawal workflows before balance changes are made around CopyBot.
- Make "research result" and "production setting" visually and textually distinct to avoid accidental promotion of in-sample results.

4/ Testing:
- Existing tests cover repo audit, CopyBot demo, hourly strategy, snapshots, and weight shift. Add tests for order reconciliation, cash movement assumptions, and stale-cache behavior.
- Add dashboard data-contract tests so published JSON snapshots cannot silently break the GitHub Pages viewer.
- Keep live API calls out of tests; use fixtures for Alpaca and Capitol responses.

5/ Everything else:
- The AGENTS instructions correctly warn that this checkout is the live source of truth. Any future development should start with `RESULTS.md`, `REALISM.md`, `STRATEGY.md`, and `TODO.md`.
- `.env`, logs, JSONL journals, local state, and `_cache/` need continuous source-control hygiene because they sit near production logic.
- The TODO is productively honest but long; split immediate live-ops backlog from research backlog when planning the next sprint.

6/ My suggetions:
1. Add dashboard JSON data-contract tests.
2. Write and validate the CopyBot cash injection/withdrawal SOP.
3. Add stale-cache and order-reconciliation tests for live bot safety.
4. Split live-ops TODOs from research TODOs for prioritization.
5. Continue small, production-safe extraction around broker/signals/reporting boundaries.
