Before starting work, also read parent instruction file `~/Dev/AGENTS.md` if it exists, then apply this repo file last.

# AGENTS.md

Working preferences for `~/Dev/trading`.

## Environment

- Follow `~/Dev/.SOPs/ENVIRONMENT_SOP.md`.
- Trading environment deltas live in [ENVIRONMENT_SOP.md](ENVIRONMENT_SOP.md).

## Versioning

- Follow `~/Dev/.SOPs/VERSIONING_SOP.md`.

## "Show Me" SOP

- Full procedure lives in [SHOW_ME_SOP.md](SHOW_ME_SOP.md).
- Apply `SHOW_ME_SOP.md` whenever the user asks to see the web app locally or on GitHub Pages.

## Research Context

- Consult [RESULTS.md](RESULTS.md) before strategy research or simulation changes so new work starts from the latest findings, known hiccups, and current conclusions.
- Consult [REALISM.md](REALISM.md) before making simulator execution or broker-compatibility changes so the realism backlog stays consistent.
- Consult [STRATEGY.md](STRATEGY.md) for the current sandbox strategy mechanics and terminology.
- Consult [RESEARCH.md](RESEARCH.md) before changing the Capitol / Khanna live path so the autonomous signal-refresh and `_cache/` layout assumptions stay aligned.
- Consult [bot_refit_results.json](bot_refit_results.json) before changing live bot defaults so production parameter updates stay aligned with the latest full-history refit.
- Consult [TODO.md](TODO.md) for the active follow-up list after finishing research or implementation work.
