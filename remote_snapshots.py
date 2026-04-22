from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import time
from io import StringIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS_DATA_DIR = HERE / "docs" / "data"
VERSION_PATH = HERE / "VERSION"
PUBLIC_VERSION_PATH = DOCS_DATA_DIR / "version.json"
DEFAULT_BUNDLE_NAME = "copybot"

SNAPSHOT_PUBLISH_INTERVAL = int(os.getenv("REMOTE_SNAPSHOT_PUBLISH_INTERVAL", "900"))
BOT_LOG_SNAPSHOT_LIMIT = int(os.getenv("REMOTE_BOT_LOG_SNAPSHOT_LIMIT", "200"))
DECISION_SNAPSHOT_LIMIT = int(os.getenv("REMOTE_DECISION_SNAPSHOT_LIMIT", "50"))
TRADE_SNAPSHOT_LIMIT = int(os.getenv("REMOTE_TRADE_SNAPSHOT_LIMIT", "50"))
GIT_TIMEOUT_SECONDS = int(os.getenv("REMOTE_SNAPSHOT_GIT_TIMEOUT", "30"))
REMOTE_SNAPSHOT_PUBLISH_ENABLED = (os.getenv("ENABLE_REMOTE_SNAPSHOT_PUBLISH", "").strip().lower() in {"1", "true", "yes", "on"})


def _bundle_dir(bundle_name: str) -> Path:
    normalized = (bundle_name or DEFAULT_BUNDLE_NAME).strip().lower() or DEFAULT_BUNDLE_NAME
    return DOCS_DATA_DIR / normalized


def _bundle_paths(bundle_name: str) -> dict[str, Path]:
    bundle_dir = _bundle_dir(bundle_name)
    return {
        "bundle_dir": bundle_dir,
        "bot_log": bundle_dir / "recent_bot.log",
        "decisions": bundle_dir / "recent_decisions.json",
        "trades": bundle_dir / "recent_trades.tsv",
        "portfolio": bundle_dir / "recent_portfolio.json",
    }


def _tail_jsonl(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        return []
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return rows[-max(1, limit) :]


def _tail_tsv(path: Path, limit: int) -> tuple[list[str], list[dict]]:
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return fieldnames, rows[-max(1, limit) :]


def _tail_lines(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return ""
    return "\n".join(lines[-max(1, limit) :]) + "\n"


def _shared_version_payload() -> dict[str, str]:
    version = VERSION_PATH.read_text(encoding="utf-8").strip() if VERSION_PATH.exists() else "0.0"
    return {
        "version": version,
        "display": f"v{version}",
    }


def _render_trades_tsv(fieldnames: list[str], rows: list[dict]) -> str:
    if not fieldnames:
        return ""
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def write_snapshot_files(
    *,
    bot_log_path: Path,
    decision_log_path: Path,
    trade_log_path: Path,
    bundle_name: str = DEFAULT_BUNDLE_NAME,
    portfolio_snapshot: dict | None = None,
    bot_log_limit: int = BOT_LOG_SNAPSHOT_LIMIT,
    decision_limit: int = DECISION_SNAPSHOT_LIMIT,
    trade_limit: int = TRADE_SNAPSHOT_LIMIT,
) -> list[Path]:
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    bundle_paths = _bundle_paths(bundle_name)
    bundle_paths["bundle_dir"].mkdir(parents=True, exist_ok=True)
    changed: list[Path] = []

    version_text = json.dumps(_shared_version_payload(), indent=2) + "\n"
    current_version_text = PUBLIC_VERSION_PATH.read_text(encoding="utf-8") if PUBLIC_VERSION_PATH.exists() else None
    if current_version_text != version_text:
        PUBLIC_VERSION_PATH.write_text(version_text, encoding="utf-8")
        changed.append(PUBLIC_VERSION_PATH)

    bot_log_text = _tail_lines(bot_log_path, bot_log_limit)
    current_bot_log_text = bundle_paths["bot_log"].read_text(encoding="utf-8") if bundle_paths["bot_log"].exists() else None
    if current_bot_log_text != bot_log_text:
        bundle_paths["bot_log"].write_text(bot_log_text, encoding="utf-8")
        changed.append(bundle_paths["bot_log"])

    decision_rows = _tail_jsonl(decision_log_path, decision_limit)
    decision_text = json.dumps(decision_rows, indent=2) + "\n"
    current_decision_text = bundle_paths["decisions"].read_text(encoding="utf-8") if bundle_paths["decisions"].exists() else None
    if current_decision_text != decision_text:
        bundle_paths["decisions"].write_text(decision_text, encoding="utf-8")
        changed.append(bundle_paths["decisions"])

    trade_fields, trade_rows = _tail_tsv(trade_log_path, trade_limit)
    trade_text = _render_trades_tsv(trade_fields, trade_rows)
    current_trade_text = bundle_paths["trades"].read_text(encoding="utf-8") if bundle_paths["trades"].exists() else None
    if current_trade_text != trade_text:
        bundle_paths["trades"].write_text(trade_text, encoding="utf-8")
        changed.append(bundle_paths["trades"])

    if portfolio_snapshot is not None:
        portfolio_text = json.dumps(portfolio_snapshot, indent=2) + "\n"
        current_portfolio_text = bundle_paths["portfolio"].read_text(encoding="utf-8") if bundle_paths["portfolio"].exists() else None
        if current_portfolio_text != portfolio_text:
            bundle_paths["portfolio"].write_text(portfolio_text, encoding="utf-8")
            changed.append(bundle_paths["portfolio"])

    return changed


class RemoteSnapshotPublisher:
    def __init__(
        self,
        *,
        bot_log_path: Path,
        decision_log_path: Path,
        trade_log_path: Path,
        bundle_name: str = DEFAULT_BUNDLE_NAME,
        portfolio_snapshot_provider=None,
        logger: logging.Logger | None = None,
        interval_seconds: int = SNAPSHOT_PUBLISH_INTERVAL,
        enabled: bool = REMOTE_SNAPSHOT_PUBLISH_ENABLED,
    ):
        self.bot_log_path = bot_log_path
        self.decision_log_path = decision_log_path
        self.trade_log_path = trade_log_path
        self.bundle_name = bundle_name
        self.portfolio_snapshot_provider = portfolio_snapshot_provider
        self.interval_seconds = max(60, interval_seconds)
        self.logger = logger or logging.getLogger("remote_snapshots")
        self.enabled = enabled
        self._last_publish_at = 0.0

    def publish_if_due(self, *, force: bool = False):
        if not self.enabled:
            return
        now = time.time()
        if not force and now - self._last_publish_at < self.interval_seconds:
            return
        self._last_publish_at = now
        self.publish_once()

    def publish_once(self):
        self._sync_branch()
        changed = write_snapshot_files(
            bot_log_path=self.bot_log_path,
            decision_log_path=self.decision_log_path,
            trade_log_path=self.trade_log_path,
            bundle_name=self.bundle_name,
            portfolio_snapshot=self.portfolio_snapshot_provider() if self.portfolio_snapshot_provider else None,
        )
        if not changed:
            return
        self._git_publish(changed)

    def _git_publish(self, changed: list[Path]):
        try:
            rel_paths = [path.relative_to(HERE).as_posix() for path in changed]
            self._run_git(["git", "add", "--", *rel_paths])
            diff = subprocess.run(
                ["git", "diff", "--cached", "--quiet", "--", *rel_paths],
                cwd=HERE,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
            if diff.returncode == 0:
                return
            branch = self._run_git(["git", "branch", "--show-current"]).strip() or "main"
            self._run_git(["git", "commit", "-m", "Update remote log snapshots"])
            self._run_git(["git", "push", "origin", branch])
            self.logger.info("Published updated remote snapshot files: %s", ", ".join(rel_paths))
        except Exception as exc:
            self.logger.error("REMOTE SNAPSHOT PUBLISH ERROR: %s", exc)

    def _sync_branch(self):
        branch = self._run_git(["git", "branch", "--show-current"]).strip() or "main"
        self._run_git(["git", "fetch", "origin", branch])
        counts = self._run_git(["git", "rev-list", "--left-right", "--count", f"{branch}...origin/{branch}"]).strip()
        ahead_str, behind_str = (counts.split() + ["0", "0"])[:2]
        ahead = int(ahead_str)
        behind = int(behind_str)
        if ahead == 0 and behind == 0:
            return
        self._run_git(["git", "pull", "--rebase", "origin", branch])

    def _run_git(self, cmd: list[str]) -> str:
        result = subprocess.run(
            cmd,
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or f"{cmd[0]} failed"
            raise RuntimeError(message)
        return result.stdout
