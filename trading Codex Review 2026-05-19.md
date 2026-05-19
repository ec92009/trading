# trading Codex Review 2026-05-19

Timestamp: 2026-05-19 02:02:56 CEST

1/ General architecture

- The TeslaBot/CopyBot split is now clearly documented and should remain stable.
- The live checkout is production source of truth, so changes must be small, observable, and compatible with running services.
- Capitol signal refresh is ready to become a reusable multi-politician framework, but only Khanna should remain live execution until risk controls are explicit.

2/ UI

- The GitHub Pages viewer has useful operator surfaces: runtime log, decision log, trade journal, and portfolio.
- Continue reducing raw plumbing IDs in favor of operator-readable state.
- The portfolio and journal views should make stale data, market-closed status, and partial-fill recovery obvious.

3/ UX

- Operational safety matters more than research cleverness.
- Cash injection/withdrawal, broker migration, and real-money experiments need SOPs before code paths.
- CopyBot retry/catch-up behavior is a critical user trust path and should stay transparent in rationales.

4/ Testing

- Add tests around incomplete-order retry caps, partial-fill status preservation, and signal refresh cache behavior.
- Research scripts should write date-stamped outputs or explicit run folders to avoid overwriting useful comparisons.
- Simulator comparisons need direct SPY and fractional-vs-whole baselines.

5/everything else

- The repo contains live logs, caches, env, and data artifacts; preserve production files and avoid cleanup without explicit intent.
- `.env` presence is sensitive and should remain uncommitted.
- Financial decisions should stay paper/simulation unless the user explicitly approves real-money steps.

6/ My suggetions:

1. Write and validate a CopyBot cash injection/withdrawal SOP.
2. Refactor Capitol refresh into a multi-politician framework while keeping only Khanna live.
3. Add regression tests for partial fills, retry caps, and incomplete-order catch-up logic.
4. Compare live-weight basket directly against SPY and fractional-vs-whole simulations.
5. Date-stamp optimizer/research outputs instead of overwriting important result files.
