# trading Codex Review 2026-05-13

Reviewed: 2026-05-13

1/ General architecture

- The repo contains live paper-trading bots, strategy research, dashboard code, tests, docs, viewer assets, and generated state/signal data.
- The README and AGENTS instructions are unusually clear about operational ownership: the always-on local checkout is the live source of truth.
- The current dirty tree includes production-path changes and an untracked `asset_policy.py`; preserve those as separate work from this review.
- The main architecture risk is still co-location of production CopyBot code, legacy TeslaBot code, research scripts, dashboard/viewer assets, and live/generated data in a flat root.

2/ UI

- The dashboard and GitHub Pages viewer should prioritize operational clarity over polish.
- Critical UI fields are current positions, pending/retry orders, last decision, last signal refresh, market state, data freshness, and paper-trading labeling.
- Viewers should make stale data obvious because both disclosure data and Alpaca state can degrade independently.
- Any control surface should have clear stop/pause/emergency-disable guidance.

3/ UX

- The highest UX requirement is operational safety: no duplicate orders, clear retries, state recovery, and visible failure modes.
- CopyBot depends on external disclosure data plus Alpaca state; users need to know when either source is stale, partial, or unavailable.
- Handoff between machines must preserve `.env`, launchd services, `_cache/`, logs, trade TSVs, and current bot state exactly as documented.
- Strategy promotion should remain gated by validation; do not auto-promote refit or optimizer artifacts.

4/ Testing

- The repo has useful tests for hourly strategy, weight shift, remote snapshots, copytrade demo, and repo audit.
- Add fake-Alpaca/fake-disclosure integration tests for CopyBot startup, refresh, retry, and no-duplicate-order behavior.
- Add tests for stale signal handling, market-closed behavior, API failures, corrupted state files, and missing cache files.
- Keep repo-audit checks strict around secrets, logs, state files, and local cache artifacts.

5/ Everything else

- Because this project can place orders, even paper orders, changes should be small and heavily verified.
- Write a production runbook for stop/start/status, log locations, service restart, handoff, and emergency disable.
- Consider gradually moving legacy, research, production, and viewer code into clearer directories without changing runtime behavior.

6/ My suggetions:

1. Add fake-Alpaca/fake-disclosure integration tests for CopyBot startup, refresh, retry, and no-duplicate-order behavior.
2. Add dashboard/viewer indicators for data freshness, last decision, pending retry queue, market state, and paper-trading status.
3. Harden state recovery tests for corrupted JSON/TSV, missing cache files, stale disclosures, and Alpaca API errors.
4. Strengthen repo-audit checks so logs, secrets, live state, and local cache artifacts cannot be committed accidentally.
5. Write a concise production runbook for stop/start/status, logs, handoff, and emergency disable.
6. Plan a low-risk directory cleanup that separates production CopyBot, legacy TeslaBot, research scripts, and viewer assets.
