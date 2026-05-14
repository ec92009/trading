# trading Codex Review 2026-05-12

Reviewed: 2026-05-12

1/ General architecture

- The repo contains live paper-trading bots, strategy research, dashboard code, logs/state files, docs, tests, and GitHub Pages viewer assets.
- The README is unusually clear about operational ownership, especially the always-on local checkout and the split between TeslaBot and CopyBot.
- The main architecture risk is co-location of production bot state/logs, research scripts, dashboard code, and tests in one flat root.
- `bot_10k.py` and `khanna_daily/` look like the current production path; older TeslaBot code should stay clearly labeled as legacy.

2/ UI

- The dashboard and GitHub Pages viewer are the user-facing surfaces.
- Trading UI should prioritize current positions, queued/pending orders, last decision, last data refresh, failure state, and paper/live labeling.
- Avoid visual polish that obscures operational status; this is a monitoring/control surface.
- Every viewer should clearly state that it is paper trading unless that ever changes.

3/ UX

- The highest UX need is operational safety: no duplicate orders, clear retry state, visible market/data freshness, and obvious stop/pause instructions.
- The CopyBot strategy depends on external disclosure data and Alpaca state. Users need to see when either source is stale or degraded.
- Manual handoff between machines should preserve launchd services, `.env`, cache, trade logs, and bot state exactly as documented.
- Strategy promotion should remain gated by validation; README already calls out not auto-promoting refit results.

4/ Testing

- There is a useful test suite for copytrade demo, hourly strategy, remote snapshots, repo audit, and weight shift strategy.
- Add tests for idempotent order retry, stale signal handling, Alpaca failure handling, market-closed behavior, and state-file corruption recovery.
- Add integration-style tests with fake Alpaca and fake disclosure data for the CopyBot path.
- Keep repo-audit tests strict about logs/secrets/state artifacts.

5/ Everything else

- `.env`, bot logs, state JSON, decision journals, and trade TSVs appeared locally. Confirm tracked vs ignored status carefully before any commits.
- Because this project can place orders, even paper orders, changes should be smaller and more heavily tested than static-site repos.
- Add a runbook for stopping, restarting, and verifying the production LaunchAgent/service.

6/ My suggetions:

1. Add fake-Alpaca/fake-disclosure integration tests for CopyBot startup, refresh, retry, and no-duplicate-order behavior.
2. Move legacy TeslaBot, research scripts, and production CopyBot modules into clearer directories without changing runtime behavior.
3. Add dashboard/viewer indicators for data freshness, last decision, pending retry queue, market state, and paper-trading status.
4. Harden state recovery tests for corrupted JSON/TSV, missing cache files, stale disclosures, and Alpaca API errors.
5. Strengthen repo-audit checks so logs, secrets, live state, and local cache artifacts cannot be committed accidentally.
6. Write a concise production runbook for stop/start/status, log locations, handoff, and emergency disable.
