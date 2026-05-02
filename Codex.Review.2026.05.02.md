# Codex Review - 2026.05.02

## Architecture

- trading is an active research and automation repo with live bot paths, strategy simulation, dashboard assets, tests, and extensive status docs.
- The current branch is `codex/max-work-handoff-20260501`, and it is current with its configured upstream.
- The main risk is breadth: live trading glue, Capitol/Khanna research, dashboard UI, cache warming, optimization, and tests all move together. Keep new work scoped to the current RESULTS/TODO direction.

## UI

- The GitHub Pages dashboard under `docs/` is the visible surface, with `docs/app.js` now large enough to deserve careful sectioning.
- Dashboard changes should prioritize scanability, current bot state, recent decisions, and failure visibility over decorative layout.
- Deleted generated `docs/data/*` files are present locally before this review; make sure the intended source of live dashboard data is clear before committing those deletions.

## UX

- The project has unusually strong handoff docs: RESULTS, REALISM, RESEARCH, STRATEGY, SUMMARY, TODO, and SOPs all help prevent context loss.
- Live or broker-compatible behavior should stay conservative and explicit. Research wins should not silently change live defaults without checking `bot_refit_results.json` and REALISM.
- The best UX for operators is a repo that says what is live, what is simulated, and what is stale without requiring archaeology.

## Misc

- Existing local source, docs, deletion, and untracked changes were present before this review and were not modified.
- No code changes were made as part of this review.
- Suggested next low-risk task: split dashboard data-generation assumptions from static dashboard rendering in the docs.
