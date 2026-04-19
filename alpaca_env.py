from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
ENV_PATH = HERE / ".env"
DEFAULT_PAPER_BASE_URL = "https://paper-api.alpaca.markets"

load_dotenv(ENV_PATH)


def _first_set(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            value = value.strip()
            if value:
                return value
    return None


def load_alpaca_credentials(profile: str | None = None) -> dict[str, str]:
    profile_key = (profile or "").strip().upper().replace("-", "_")
    if profile_key == "10K":
        api_key = _first_set("ALPACA_10K_API_KEY", "ALPACA_API_KEY")
        secret_key = _first_set("ALPACA_10K_SECRET_KEY", "ALPACA_SECRET_KEY")
        base_url = _first_set("ALPACA_10K_BASE_URL", "ALPACA__10K_BASE_URL", "ALPACA_BASE_URL")
    else:
        api_key = _first_set("ALPACA_API_KEY")
        secret_key = _first_set("ALPACA_SECRET_KEY")
        base_url = _first_set("ALPACA_BASE_URL")
    return {
        "api_key": api_key or "",
        "secret_key": secret_key or "",
        "base_url": base_url or DEFAULT_PAPER_BASE_URL,
    }
