# AGENTS.md

Working preferences for `~/Dev/trading`.

## Environment

- Use the repo virtualenv for Python commands in this workspace.
- Prefer `./.venv/bin/python` over system `python`/`python3`.
- Prefer `./.venv/bin/pytest` for tests and `./.venv/bin/pip` for package installs when needed.

## Versioning

- Full procedure lives in [VERSIONING_SOP.md](VERSIONING_SOP.md).
- Apply `VERSIONING_SOP.md` whenever bot-facing version numbers or release badges change.
- Do not assume simulation-only research changes need a version bump unless they also ship a bot/UI-facing change.
- For each completed web-app / bot-facing delivery cycle, bump the shared version, keep the bot and web app on the same version source, and report the three viewer URLs:
- localhost URL
- LAN URL
- public GitHub Pages URL
- Also report the new visible version at the end of the cycle.

## "Show Me" SOP

- When the user asks to "show me" the web app, default to running the local static viewer server for `docs/`.
- If the user also wants the public site updated, push the committed `main` branch to GitHub so GitHub Pages can deploy `/docs`.
- Report all three viewer URLs in the handoff:
- localhost URL
- LAN URL
- public GitHub Pages URL
- Also report the exact visible UI version the user should expect on those surfaces.
- Be explicit about scope: uncommitted local changes are not part of the GitHub Pages deploy unless they are committed first.

## Research Context

- Consult [RESULTS.md](RESULTS.md) before strategy research or simulation changes so new work starts from the latest findings, known hiccups, and current conclusions.
- Consult [REALISM.md](REALISM.md) before making simulator execution or broker-compatibility changes so the realism backlog stays consistent.
- Consult [STRATEGY.md](STRATEGY.md) for the current sandbox strategy mechanics and terminology.
- Consult [RESEARCH.md](RESEARCH.md) before changing the Capitol / Khanna live path so the autonomous signal-refresh and `_cache/` layout assumptions stay aligned.
- Consult [bot_refit_results.json](bot_refit_results.json) before changing live bot defaults so production parameter updates stay aligned with the latest full-history refit.
- Consult [TODO.md](TODO.md) for the active follow-up list after finishing research or implementation work.
