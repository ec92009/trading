# trading Codex Review 2026-05-06

Timestamp: 2026-05-06 02:02 CEST

## 1/ General architecture

- The production/live source-of-truth constraint is central; keep changes small and preserve running bot state, logs, caches, and launchd services.
- The TeslaBot/CopyBot split is documented and should remain explicit until a deliberate consolidation is approved.
- Push more shared concerns into reusable modules: broker access, order logging, market data, snapshot publishing, risk checks, and decision journaling.
- Treat strategy research artifacts separately from production runtime code so experiments cannot silently change live behavior.

## 2/ UI

- The docs viewer/dashboard should make bot identity, account scope, last heartbeat, signal age, pending orders, and latest error immediately visible.
- Separate TeslaBot and CopyBot panels visually; they have different purposes and risk profiles.
- Add stale-data warnings for viewer snapshots and Capitol Trades signal refresh age.

## 3/ UX

- Operators need "what should I do now?" cues: running normally, market closed, auth failure, stale signals, pending fills, partial order retry, or manual intervention required.
- Keep restart/recovery steps explicit in docs and avoid depending on remembered local commands.
- Add a read-only status command that is safe to run while bots are live.

## 4/ Testing

- Expand tests around duplicate order prevention, restart recovery, partial fills, signal refresh failures, and market-closed behavior.
- Add simulator/regression tests that lock current CopyBot assumptions before tuning strategy parameters.
- Keep tests from touching live `.env`, `_cache/`, `trades*.tsv`, or running bot files.

## 5/ Everything else

- Current uncommitted changes in `TODO.md` plus review files should be separated from live bot changes before commit.
- Large JSON result sets and logs should be curated so repo searches stay useful.
- Keep `RESULTS.md`, `REALISM.md`, and `TODO.md` synchronized after every research or runtime change.

## 6/ My suggetions:

1. Add a safe read-only status command for both TeslaBot and CopyBot.
2. Add regression tests for duplicate suppression, restart recovery, partial fills, and stale signal handling.
3. Add dashboard stale-data and bot-health warnings.
4. Separate research artifacts from production runtime paths more clearly.
5. Reconcile current uncommitted docs/review changes before further live-bot work.
