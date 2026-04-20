from __future__ import annotations

import os

os.environ.setdefault("ALPACA_PROFILE", "10K")
os.environ.setdefault("BOT_LOG_SUFFIX", "10k")
os.environ.setdefault("ENABLE_REMOTE_SNAPSHOT_PUBLISH", "1")

from khanna_daily.live import main


if __name__ == "__main__":
    main()
