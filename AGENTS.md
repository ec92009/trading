# AGENTS.md

Working preferences for `~/Dev/trading`.

## Production Source Of Truth

- David's always-on Mac checkout at `/Users/ecohen/Dev/trading` is the production/live source of truth.
- Treat GitHub as publication and history, not as a replacement for the live operations host.
- Do not treat `codex/max-work-handoff-20260501` or `MAX_WORK.md` as canonical; that branch is historical drift from stale `main`.
- Do not force-push, rebase over, or replace the live bot checkout.
- Preserve running bots, local logs, `_cache/`, `trades*.tsv`, and launchd services unless David explicitly directs otherwise.
- Future changes should be small patches based on current `main`, or explicit instructions for David/Codex to apply locally.
- Do not rename `bot_10k.py` to `copybot.py` or move snapshot paths unless David explicitly approves that refactor.
- Preserve the current split:
  - TeslaBot: `bot.py`
  - CopyBot: `bot_10k.py` -> `khanna_daily/live.py`
  - viewer data: `docs/data/copybot` and `docs/data/teslabot`
- For live-ops work, coordinate around David's machine first, then push or publish after verification.

## Environment

- Full procedure lives in [ENVIRONMENT_SOP.md](ENVIRONMENT_SOP.md).
- Apply `ENVIRONMENT_SOP.md` for Python commands, tests, and package installs in this workspace.

## Versioning

- Full procedure lives in [VERSIONING_SOP.md](VERSIONING_SOP.md).
- Apply `VERSIONING_SOP.md` whenever TeslaBot- or CopyBot-facing version numbers or release badges change.

## "Show Me" SOP

- Full procedure lives in [SHOW_ME_SOP.md](SHOW_ME_SOP.md).
- Apply `SHOW_ME_SOP.md` whenever the user asks to see the web app locally or on GitHub Pages.

## Research Context

- Consult [RESULTS.md](RESULTS.md) before strategy research or simulation changes so new work starts from the latest findings, known hiccups, and current conclusions.
- Consult [REALISM.md](REALISM.md) before making simulator execution or broker-compatibility changes so the realism backlog stays consistent.
- Consult [STRATEGY.md](STRATEGY.md) for the current sandbox strategy mechanics and terminology.
- Consult [RESEARCH.md](RESEARCH.md) before changing the Capitol / Khanna CopyBot path so the autonomous signal-refresh and `_cache/` layout assumptions stay aligned.
- Consult [bot_refit_results.json](bot_refit_results.json) before changing TeslaBot / basket defaults so the production parameter handoff stays aligned with the latest full-history refit.
- Consult [TODO.md](TODO.md) for the active follow-up list after finishing research or implementation work.
