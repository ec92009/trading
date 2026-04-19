"""
Migrate legacy hashed hourly cache files into year/quarter folders.

Examples:
    ./.venv/bin/python migrate_hourly_cache_layout.py
    ./.venv/bin/python migrate_hourly_cache_layout.py --remove-legacy
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from hourly_strategy import SYMBOL_CACHE_DIR, _quarter_label


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove-legacy", action="store_true")
    return parser.parse_args()


def _target_path(symbol_stem: str, quarter_start: str) -> Path:
    return SYMBOL_CACHE_DIR / quarter_start[:4] / _quarter_label(quarter_start) / f"{symbol_stem}.json"


def _parse_legacy_name(path: Path) -> tuple[str, str, str] | None:
    if path.suffix != ".json":
        return None
    try:
        symbol_stem, quarter_start, quarter_end, _digest = path.stem.rsplit("_", 3)
    except ValueError:
        return None
    if len(quarter_start) != 10 or len(quarter_end) != 10:
        return None
    return symbol_stem, quarter_start, quarter_end


def main() -> None:
    args = parse_args()
    moved = 0
    skipped = 0
    removed = 0

    for path in sorted(SYMBOL_CACHE_DIR.glob("*.json")):
        parsed = _parse_legacy_name(path)
        if parsed is None:
            skipped += 1
            continue
        symbol_stem, quarter_start, _quarter_end = parsed
        target = _target_path(symbol_stem, quarter_start)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(path, target)
            moved += 1
        else:
            skipped += 1
        if args.remove_legacy:
            path.unlink()
            removed += 1

    print(
        {
            "moved": moved,
            "skipped": skipped,
            "removed_legacy": removed,
        }
    )


if __name__ == "__main__":
    main()
