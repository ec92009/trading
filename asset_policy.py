from __future__ import annotations

REPO_CRYPTO_SYMBOLS = {"BTC/USD"}
ALPACA_TO_REPO_SYMBOL = {"BTCUSD": "BTC/USD"}
CRYPTO_QTY_PRECISION = 8
EQUITY_QTY_PRECISION = 6


def normalize_tracked_symbol(symbol: str) -> str:
    return ALPACA_TO_REPO_SYMBOL.get(symbol, symbol)


def is_crypto_symbol(symbol: str) -> bool:
    normalized = normalize_tracked_symbol(symbol)
    return normalized in REPO_CRYPTO_SYMBOLS or "/" in normalized


def asset_class_for_symbol(symbol: str) -> str:
    return "crypto" if is_crypto_symbol(symbol) else "stock"


def qty_precision_for_asset_class(asset_class: str) -> int:
    return CRYPTO_QTY_PRECISION if asset_class.strip().lower() == "crypto" else EQUITY_QTY_PRECISION


def qty_precision_for_symbol(symbol: str) -> int:
    return qty_precision_for_asset_class(asset_class_for_symbol(symbol))


def time_in_force_for_asset_class(asset_class: str, time_in_force_enum):
    return time_in_force_enum.GTC if asset_class.strip().lower() == "crypto" else time_in_force_enum.DAY


def time_in_force_for_symbol(symbol: str, time_in_force_enum):
    return time_in_force_for_asset_class(asset_class_for_symbol(symbol), time_in_force_enum)
