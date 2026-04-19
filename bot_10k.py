from __future__ import annotations

import os

os.environ.setdefault("ALPACA_PROFILE", "10K")
os.environ.setdefault("BOT_LOG_SUFFIX", "10k")

from bot import BOTS, Bot, PortfolioManager


if __name__ == "__main__":
    bots = [Bot(cfg) for cfg in BOTS]
    PortfolioManager(bots).run()
