# trading Codex Review 2026-05-05

Generated: 2026-05-05 10:36:54 CEST

1/ General architecture

- trading has clear operational docs and a useful split between legacy TeslaBot and primary CopyBot. The production-source-of-truth warning in `AGENTS.md` is important and should remain.
- `khanna_daily/live.py` is doing live manager orchestration, signal refresh, simulation, target book creation, order sync, state persistence, and snapshot publishing. Split execution concerns so live trading risk is easier to review.
- The repo contains many local runtime artifacts and pre-existing dirty changes. I did not alter those unrelated files.

2/ UI

- The GitHub Pages viewer and local dashboard are important for live confidence. Keep UI focused on portfolio state, target vs actual, pending orders, skipped symbols, latest signal refresh, and last broker heartbeat.
- Add obvious stale-data warnings when snapshots, signals, or market data are older than expected.

3/ UX

- Operations are documented, but the safest user experience is "what is the bot doing right now and why?" Put the latest decision reason near every order attempt and skipped order.
- Add a manual pause/resume and read-only health command if not already present.

4/ Testing

- Existing tests cover strategy pieces. Add tests for live manager state persistence, signature changes, incomplete-order retry limits, stale signal handling, and snapshot payload schema.
- Broker interactions should be covered by fakes with explicit paper/live profile assertions.

5/ Everything else

- Archive already contains a prior trading review. This run wrote a fresh root review.
- Be strict about not committing `.env`, logs, state files, broker cache, or generated runtime data.

6/ My suggetions:

1. Split `CopyTradeLiveManager` into signal refresh, simulation target, broker reconciliation, and snapshot publisher collaborators.
2. Add stale-data warnings to dashboard/viewer snapshots.
3. Add fake-broker tests for retry limits and profile safety.
4. Add a health command summarizing bot state without placing orders.
5. Audit `.gitignore` for logs, state, cache, and generated runtime artifacts.
