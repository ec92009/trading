# MAX Work Handoff

Generated: 2026-05-01 17:29 CEST

This repo is not safe to fast-forward automatically right now. Local `main` has
no local-only commits, but it is 755 commits behind `origin/main` after fetch:

- local `HEAD`: `30b7e9d Split workspace SOP docs and prefer uv`
- remote `origin/main`: `1ce7595 Update remote log snapshots`
- local ahead/behind: `0 / 755`

The working tree also contains a large uncommitted local work batch. David
should decide whether to preserve, cherry-pick, or discard pieces before anyone
pulls, merges, rebases, or force-pushes.

## Why Autosync Stopped

Upstream changes overlap 21 dirty local paths:

- `README.md`
- `REALISM.md`
- `RESEARCH.md`
- `RESULTS.md`
- `STRATEGY.md`
- `SUMMARY.md`
- `VERSION`
- `alpaca_env.py`
- `bot.py`
- `docs/README.md`
- `docs/app.js`
- `docs/data/recent_bot.log`
- `docs/data/recent_decisions.json`
- `docs/data/recent_portfolio.json`
- `docs/data/version.json`
- `docs/index.html`
- `docs/styles.css`
- `khanna_daily/live.py`
- `remote_snapshots.py`
- `tests/test_remote_snapshots.py`
- `tests/test_repo_audit.py`

Because those files changed locally and upstream, an automatic fast-forward
would overwrite or entangle live bot, viewer, snapshot, version, and test work.

## Local Work Summary

The local batch appears to be a bot/runtime split plus research-validation
update:

- renames the old `10K`/`bot_10k.py` framing toward `CopyBot` and `copybot.py`
- introduces runtime-family paths for `TeslaBot` and `CopyBot/Khanna`
- moves committed GitHub Pages snapshots from `docs/data/` into bot-specific
  folders such as `docs/TeslaBot/` and `docs/CopyBot/Khanna/`
- updates the viewer to choose between `TeslaBot`, `CopyBot / Khanna`, and a
  placeholder `CopyBot / Mullin` feed
- bumps `VERSION` from `53.0` to `54.0`
- adds recent-order reconciliation/backfill behavior in `bot.py`
- adds lazy Alpaca market-data clients in `hourly_strategy.py`
- expands benchmark output to include basket buy-and-hold, `SPY`, rebalance-only,
  stop/trigger-only, and stop/trigger-plus-rebalance contenders
- adds walk-forward validation artifacts and tests
- updates README, RESULTS, STRATEGY, RESEARCH, REALISM, SUMMARY, and docs to
  describe the new CopyBot/runtime/viewer direction

## Modified Or Deleted Tracked Files

```text
M  .gitignore
M  README.md
M  REALISM.md
M  RESEARCH.md
M  RESULTS.md
M  STRATEGY.md
M  SUMMARY.md
M  VERSION
M  alpaca_env.py
M  bot.py
D  bot_10k.py
M  copytrade_live.py
M  docs/README.md
M  docs/app.js
D  docs/data/recent_bot.log
D  docs/data/recent_decisions.json
D  docs/data/recent_portfolio.json
D  docs/data/recent_trades.tsv
D  docs/data/version.json
M  docs/index.html
M  docs/styles.css
M  hourly_strategy.py
M  hourly_strategy_results.json
M  khanna_daily/__init__.py
M  khanna_daily/live.py
M  optimize_hourly_strategies.py
M  remote_snapshots.py
M  tests/test_hourly_strategy.py
M  tests/test_remote_snapshots.py
M  tests/test_repo_audit.py
M  trade_log.py
```

Tracked diff stat at handoff:

```text
31 files changed, 1560 insertions(+), 1933 deletions(-)
```

## Untracked Local Files

```text
backfill_capitol_trades.py
bot_runtime.py
copybot.py
docs/CopyBot/Khanna/recent_decisions.json
docs/CopyBot/Khanna/recent_portfolio.json
docs/CopyBot/Khanna/recent_trades.tsv
docs/CopyBot/Khanna/version.json
docs/CopyBot/Mullin/recent_decisions.json
docs/CopyBot/Mullin/recent_trades.tsv
docs/CopyBot/Mullin/version.json
docs/TeslaBot/recent_decisions.json
docs/TeslaBot/recent_trades.tsv
docs/TeslaBot/version.json
pyproject.toml
tests/test_capitol_backfill.py
tests/test_walk_forward_hourly.py
walk_forward_hourly.py
walk_forward_hourly_results.json
warm_hourly_cache.py
```

Note: `find` also sees untracked runtime logs under `docs/CopyBot/*/recent_bot.log`
and `docs/TeslaBot/recent_bot.log`. `git ls-files --others --exclude-standard`
does not list those logs, which suggests they are ignored by current ignore
rules.

## Upstream State

The fetched upstream head is `1ce7595 Update remote log snapshots`.

The latest visible upstream commits are all snapshot refreshes:

```text
1ce7595 Update remote log snapshots
c8a2a36 Update remote log snapshots
3219eb4 Update remote log snapshots
2f734b3 Update remote log snapshots
c437879 Update remote log snapshots
5de714c Update remote log snapshots
705c638 Update remote log snapshots
7f0528e Update remote log snapshots
2599406 Update remote log snapshots
c146b19 Update remote log snapshots
c58c3ee Update remote log snapshots
45c98be Update remote log snapshots
9cd6f72 Update remote log snapshots
591bae6 Update remote log snapshots
427bf4d Update remote log snapshots
```

## Decision Needed From David

Recommended decision order:

1. Decide whether the local CopyBot/runtime split is still wanted.
2. If yes, preserve source/config/test/doc changes first, but treat committed
   snapshot files as volatile and reconcile them against `origin/main`.
3. Decide whether `VERSION` should remain `54.0` or follow the current upstream
   release state.
4. Review whether deleting `bot_10k.py` is acceptable now that `copybot.py` is
   the intended entrypoint.
5. Review whether `docs/data/*` should be removed permanently or retained as a
   compatibility redirect/source.
6. After the decision, merge manually or branch-clean with explicit conflict
   resolution. Do not automatic-pull this worktree.

## Suggested Next Commands

For a reviewer who wants to inspect without changing anything:

```bash
cd /Users/ecohen/Dev/trading
git status -sb
git diff --stat
git diff --name-status
git diff HEAD..origin/main --stat
```

For integration, create a fresh branch/worktree from `origin/main` and
cherry-pick or manually copy only the approved pieces. This current worktree is
useful evidence, but it should not be blindly merged.
