"""
Unified local control panel for the trading bot.

Features:
- Alpaca paper credentials setup + connection test
- Asset add / edit / remove with per-asset trading config
- Live portfolio dashboard, open orders, and trade history
- launchd bot start / stop / reload + install / repair controls

Usage:
    python3 dashboard.py
    python3 dashboard.py --no-browser
    python3 dashboard.py --no-browser --port 8091
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

import trade_log
from add_asset import fetch_account, search_assets, validate_symbol

HERE = Path(__file__).parent
ENV_PATH = HERE / ".env"
BOT_PATH = HERE / "bot.py"
BOT_LOG_PATH = HERE / "bot.log"
VERSION_PATH = HERE / "VERSION"
PLIST_PATH = Path.home() / "Library/LaunchAgents/com.trading.bot.plist"
PORT = 8080
ACTIVE_PORT = PORT

load_dotenv(ENV_PATH)

COLORS = [
    "#d8b27a",
    "#73b7ff",
    "#71d6ad",
    "#ff8f70",
    "#c4a7ff",
    "#f0c96a",
    "#89dceb",
    "#f38ba8",
]

ASSET_DEFAULTS = {
    "initial_notional": 50.0,
    "ladder_notional": 50.0,
    "target_weight": 0.20,
    "stop_pct": 0.95,
    "trail_trigger": 1.10,
    "trail_step": 1.05,
    "trail_stop": 0.95,
    "ladder1_pct": 0.925,
    "ladder2_pct": 0.850,
    "poll_interval": 30,
}

EDITABLE_FIELDS = [
    "initial_notional",
    "ladder_notional",
    "target_weight",
    "stop_pct",
    "trail_trigger",
    "trail_step",
    "trail_stop",
    "ladder1_pct",
    "ladder2_pct",
    "poll_interval",
]


def read_env_settings(path: Path = ENV_PATH) -> dict[str, str]:
    settings = {
        "ALPACA_API_KEY": "",
        "ALPACA_SECRET_KEY": "",
        "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
    }
    if not path.exists():
        return settings
    for line in path.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in settings:
            settings[key] = value.strip()
    return settings


def read_version(path: Path = VERSION_PATH) -> str:
    if not path.exists():
        return "0.0"
    return path.read_text().strip() or "0.0"


def visible_version(path: Path = VERSION_PATH) -> str:
    version = read_version(path)
    return version if version.startswith("v") else f"v{version}"


def save_env_settings(api_key: str, secret_key: str, base_url: str, path: Path = ENV_PATH):
    values = {
        "ALPACA_API_KEY": api_key.strip(),
        "ALPACA_SECRET_KEY": secret_key.strip(),
        "ALPACA_BASE_URL": base_url.strip() or "https://paper-api.alpaca.markets",
    }
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    os.environ.update(values)


def mask_value(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return value[0] + "..." + value[-1]
    return f"{value[:4]}...{value[-4:]}"


def lan_ip() -> str | None:
    for device in ("en0", "en1"):
        try:
            result = subprocess.run(
                ["ipconfig", "getifaddr", device],
                capture_output=True,
                text=True,
                check=False,
            )
            ip = result.stdout.strip()
            if result.returncode == 0 and ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        return None
    return None


def credentials_configured() -> bool:
    env = read_env_settings()
    return bool(env["ALPACA_API_KEY"] and env["ALPACA_SECRET_KEY"])


def split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_string = False
    quote = ""
    for ch in text:
        if in_string:
            buf.append(ch)
            if ch == quote:
                in_string = False
            continue
        if ch in {'"', "'"}:
            in_string = True
            quote = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def load_portfolio_anchor(bot_text: str) -> float | None:
    match = re.search(r"^_P\s*=\s*([0-9]+(?:\.[0-9]+)?)", bot_text, re.MULTILINE)
    return float(match.group(1)) if match else None


def safe_eval_numeric(expr: str, portfolio_anchor: float | None) -> float | None:
    expr = expr.strip()
    if expr.startswith(("'", '"')):
        return None
    try:
        return float(expr)
    except Exception:
        pass
    allowed = {"round": round, "_P": portfolio_anchor}
    try:
        value = eval(expr, {"__builtins__": {}}, allowed)  # noqa: S307
        return float(value)
    except Exception:
        return None


def format_numeric(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def bot_source(path: Path = BOT_PATH) -> str:
    return path.read_text()


def botconfig_lines(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    in_bots = False
    results: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        if line.strip().startswith("BOTS = ["):
            in_bots = True
            continue
        if in_bots and line.strip() == "]":
            break
        if in_bots and "BotConfig(" in line:
            results.append((idx, line))
    return results


def parse_bot_line(line: str, portfolio_anchor: float | None, color_idx: int) -> dict:
    start = line.index("BotConfig(") + len("BotConfig(")
    end = line.rindex(")")
    body = line[start:end]
    parsed: dict[str, object] = {
        "raw_line": line,
        "color": COLORS[color_idx % len(COLORS)],
    }
    for part in split_top_level(body):
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        parsed[f"{key}_expr"] = raw
        if raw.startswith(("'", '"')):
            parsed[key] = raw.strip('"').strip("'")
        else:
            parsed[key] = safe_eval_numeric(raw, portfolio_anchor)
    return parsed


def load_bots(path: Path = BOT_PATH) -> list[dict]:
    text = bot_source(path)
    portfolio_anchor = load_portfolio_anchor(text)
    bots = []
    for idx, (_line_no, line) in enumerate(botconfig_lines(text)):
        bot = parse_bot_line(line, portfolio_anchor, idx)
        bot["symbol"] = str(bot.get("symbol", ""))
        bot["asset_class"] = str(bot.get("asset_class", "stock"))
        for field, default in ASSET_DEFAULTS.items():
            if bot.get(field) is None:
                bot[field] = default
        bots.append(bot)
    return bots


def build_bot_line(config: dict) -> str:
    parts = [
        f'symbol="{config["symbol"]}"',
        f'asset_class="{config["asset_class"]}"',
        f'initial_notional={format_numeric(float(config["initial_notional"]))}',
        f'ladder_notional={format_numeric(float(config["ladder_notional"]))}',
    ]
    for field in EDITABLE_FIELDS[2:]:
        value = config[field]
        default = ASSET_DEFAULTS[field]
        if float(value) != float(default):
            parts.append(
                f"{field}={int(value) if field == 'poll_interval' else format_numeric(float(value))}"
            )
    return f"    BotConfig({', '.join(parts)}),"


def write_bots(configs: list[dict], path: Path = BOT_PATH):
    text = bot_source(path)
    lines = text.splitlines()
    bot_lines = botconfig_lines(text)
    if not bot_lines:
        raise RuntimeError("Could not locate BOTS list in bot.py")
    start = bot_lines[0][0]
    end = bot_lines[-1][0]
    replacement = [build_bot_line(cfg) for cfg in configs]
    new_lines = lines[:start] + replacement + lines[end + 1 :]
    path.write_text("\n".join(new_lines) + "\n")


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def coerce_asset_config(payload: dict, current_symbol: str | None = None) -> dict:
    symbol = normalize_symbol(payload.get("symbol", ""))
    if not symbol:
        raise RuntimeError("Symbol is required.")
    asset_class = "crypto" if "/" in symbol else "stock"
    bots = load_bots()
    existing = {bot["symbol"].upper() for bot in bots}
    if current_symbol:
        existing.discard(current_symbol.upper())
    if symbol in existing:
        raise RuntimeError(f"{symbol} is already being watched.")

    error = validate_symbol(symbol, asset_class)
    if error:
        raise RuntimeError(error)

    initial = float(payload.get("initial_notional", "") or 0)
    ladder = float(payload.get("ladder_notional", "") or initial)
    if initial <= 0 or ladder <= 0:
        raise RuntimeError("Initial and ladder notionals must be positive.")

    config = {
        "symbol": symbol,
        "asset_class": asset_class,
        "initial_notional": round(initial, 2),
        "ladder_notional": round(ladder, 2),
        "target_weight": float(payload.get("target_weight", ASSET_DEFAULTS["target_weight"])),
        "stop_pct": float(payload.get("stop_pct", ASSET_DEFAULTS["stop_pct"])),
        "trail_trigger": float(payload.get("trail_trigger", ASSET_DEFAULTS["trail_trigger"])),
        "trail_step": float(payload.get("trail_step", ASSET_DEFAULTS["trail_step"])),
        "trail_stop": float(payload.get("trail_stop", ASSET_DEFAULTS["trail_stop"])),
        "ladder1_pct": float(payload.get("ladder1_pct", ASSET_DEFAULTS["ladder1_pct"])),
        "ladder2_pct": float(payload.get("ladder2_pct", ASSET_DEFAULTS["ladder2_pct"])),
        "poll_interval": int(float(payload.get("poll_interval", ASSET_DEFAULTS["poll_interval"]))),
    }
    if config["poll_interval"] <= 0:
        raise RuntimeError("Poll interval must be positive.")
    if config["target_weight"] < 0:
        raise RuntimeError("Target weight must be non-negative.")
    return config


def add_asset_from_request(payload: dict) -> str:
    config = coerce_asset_config(payload)
    bots = load_bots()
    bots.append(config)
    write_bots(bots)
    return maybe_reload_after_config(f'{config["symbol"]} added.')


def update_asset_from_request(symbol: str, payload: dict) -> str:
    bots = load_bots()
    updated = coerce_asset_config(payload, current_symbol=symbol)
    for idx, bot in enumerate(bots):
        if bot["symbol"].upper() == symbol.upper():
            bots[idx] = updated
            write_bots(bots)
            return maybe_reload_after_config(f'{symbol} updated.')
    raise RuntimeError(f"{symbol} not found.")


def remove_asset(symbol: str) -> str:
    bots = load_bots()
    remaining = [bot for bot in bots if bot["symbol"].upper() != symbol.upper()]
    if len(remaining) == len(bots):
        raise RuntimeError(f"{symbol} not found.")
    write_bots(remaining)
    return maybe_reload_after_config(f"{symbol} removed.")


def maybe_reload_after_config(prefix: str) -> str:
    if not PLIST_PATH.exists():
        return f"{prefix} Saved to bot.py. LaunchAgent not found, so the bot was not reloaded."
    try:
        message = reload_service()
        return f"{prefix} {message}"
    except Exception as exc:
        return f"{prefix} Saved to bot.py, but reload failed: {exc}"


def service_label(plist_path: Path = PLIST_PATH) -> str:
    if plist_path.exists():
        try:
            data = plistlib.loads(plist_path.read_bytes())
            if data.get("Label"):
                return str(data["Label"])
        except Exception:
            pass
    return "com.trading.bot"


def run_launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def launchctl_list() -> str:
    result = run_launchctl("list")
    return result.stdout if result.returncode == 0 else ""


def launch_agent_program() -> str:
    venv_python = HERE / ".venv" / "bin" / "python"
    return str(venv_python if venv_python.exists() else Path(sys.executable))


def launch_agent_dict() -> dict:
    return {
        "Label": "com.trading.bot",
        "ProgramArguments": [launch_agent_program(), str(BOT_PATH)],
        "WorkingDirectory": str(HERE),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(BOT_LOG_PATH),
        "StandardErrorPath": str(BOT_LOG_PATH),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }


def install_or_repair_launch_agent() -> str:
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(plistlib.dumps(launch_agent_dict()))
    return "LaunchAgent installed or repaired."


def get_service_status() -> dict[str, object]:
    label = service_label()
    available = PLIST_PATH.exists()
    loaded = False
    running = False
    pid = None
    for line in launchctl_list().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] == label:
            loaded = True
            pid = parts[0]
            running = pid not in {"-", "0"}
            break
    detail = (
        "LaunchAgent missing"
        if not available
        else "Running" if running
        else "Loaded" if loaded
        else "Stopped"
    )
    return {
        "available": available,
        "loaded": loaded,
        "running": running,
        "pid": pid,
        "label": label,
        "detail": detail,
        "path": str(PLIST_PATH),
        "program": launch_agent_program(),
    }


def start_service():
    if not PLIST_PATH.exists():
        raise RuntimeError(f"LaunchAgent not found at {PLIST_PATH}")
    status = get_service_status()
    if status["loaded"]:
        return "Bot already loaded."
    result = run_launchctl("load", str(PLIST_PATH))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "launchctl load failed").strip())
    return "Bot started."


def stop_service():
    if not PLIST_PATH.exists():
        raise RuntimeError(f"LaunchAgent not found at {PLIST_PATH}")
    status = get_service_status()
    if not status["loaded"]:
        return "Bot already stopped."
    uid = subprocess.check_output(["id", "-u"]).decode().strip()
    result = run_launchctl("bootout", f"gui/{uid}", str(PLIST_PATH))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "launchctl bootout failed").strip())
    return "Bot stopped."


def reload_service():
    if not PLIST_PATH.exists():
        raise RuntimeError(f"LaunchAgent not found at {PLIST_PATH}")
    uid = subprocess.check_output(["id", "-u"]).decode().strip()
    if get_service_status()["loaded"]:
        result = run_launchctl("bootout", f"gui/{uid}", str(PLIST_PATH))
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "launchctl bootout failed").strip())
    result = run_launchctl("load", str(PLIST_PATH))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "launchctl load failed").strip())
    return "Bot reloaded."


def tail_log(lines: int = 30) -> str:
    if not BOT_LOG_PATH.exists():
        return "No bot log yet."
    data = BOT_LOG_PATH.read_text().splitlines()
    return "\n".join(data[-lines:]) if data else "No bot log yet."


def parse_history(symbol: str) -> tuple[list[str], list[float], list[float], float | None]:
    tag = symbol.replace("/", "")
    today = date.today()
    times, prices, floors = [], [], []
    entry = None
    if not BOT_LOG_PATH.exists():
        return times, prices, floors, entry
    lines = BOT_LOG_PATH.read_text().splitlines()
    last_start = 0
    for idx, line in enumerate(lines):
        if f"[{tag}]" in line and "BOT STARTED" in line:
            last_start = idx
    for line in lines[last_start:]:
        if f"[{tag}]" not in line:
            continue
        match = re.match(r"(\d{2}:\d{2}:\d{2})", line)
        if not match:
            continue
        dt = datetime.combine(today, datetime.strptime(match.group(1), "%H:%M:%S").time())
        if "Entry" in line and entry is None:
            price = re.search(r"\$([0-9,]+\.?\d*)", line)
            if price:
                entry = float(price.group(1).replace(",", ""))
            continue
        price_match = re.search(r"price=\$([0-9,]+\.?\d*)", line)
        floor_match = re.search(r"floor=\$([0-9,]+\.?\d*)", line)
        if price_match and floor_match:
            times.append(dt.strftime("%Y-%m-%dT%H:%M:%S"))
            prices.append(float(price_match.group(1).replace(",", "")))
            floors.append(float(floor_match.group(1).replace(",", "")))
    return times, prices, floors, entry


def alpaca_clients():
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
    from alpaca.trading.client import TradingClient

    env = read_env_settings()
    key = env["ALPACA_API_KEY"]
    secret = env["ALPACA_SECRET_KEY"]
    if not key or not secret:
        raise RuntimeError("Add Alpaca paper credentials first.")
    return (
        TradingClient(api_key=key, secret_key=secret, paper=True),
        StockHistoricalDataClient(api_key=key, secret_key=secret),
        CryptoHistoricalDataClient(api_key=key, secret_key=secret),
    )


def test_credentials() -> dict:
    trading, _stock, _crypto = alpaca_clients()
    account = trading.get_account()
    return {
        "account_status": str(account.status).split(".")[-1].title(),
        "portfolio_value": float(account.portfolio_value),
        "buying_power": float(account.buying_power),
        "cash": float(account.cash),
    }


def fetch_data() -> dict:
    from alpaca.data.requests import CryptoLatestQuoteRequest, StockLatestQuoteRequest
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    trading, stock_data, crypto_data = alpaca_clients()
    account = trading.get_account()
    positions = {p.symbol: p for p in trading.get_all_positions()}
    bots = load_bots()

    assets = {}
    for bot in bots:
        sym = bot["symbol"]
        tag = sym.replace("/", "")
        try:
            if bot["asset_class"] == "crypto":
                q = crypto_data.get_crypto_latest_quote(CryptoLatestQuoteRequest(symbol_or_symbols=sym))[sym]
            else:
                q = stock_data.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=sym))[sym]
            ask = float(q.ask_price or 0)
            bid = float(q.bid_price or 0)
            live = (ask + bid) / 2 if (ask and bid) else ask or bid
        except Exception:
            live = 0.0

        pos = positions.get(tag)
        avg_entry = float(pos.avg_entry_price) if pos else None
        mkt_val = float(pos.market_value) if pos else 0.0
        pl = float(pos.unrealized_pl) if pos else 0.0
        pl_pct = float(pos.unrealized_plpc) * 100 if pos else 0.0
        qty = float(pos.qty) if pos else 0.0
        if pos:
            pos_current = float(getattr(pos, "current_price", 0) or 0)
            if pos_current > 0:
                live = pos_current

        times, prices, floors, log_entry = parse_history(sym)
        entry = avg_entry or log_entry or live
        assets[sym] = {
            "color": bot["color"],
            "asset_class": bot["asset_class"],
            "trail_trigger": float(bot["trail_trigger"]),
            "times": times,
            "prices": prices,
            "floors": floors,
            "entry": entry,
            "live": live,
            "mkt_val": mkt_val,
            "pl": pl,
            "pl_pct": pl_pct,
            "qty": qty,
        }

    try:
        open_orders = trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    except Exception:
        open_orders = []

    orders = []
    for order in open_orders:
        orders.append(
            {
                "symbol": order.symbol,
                "side": str(order.side).replace("OrderSide.", ""),
                "status": str(order.status).replace("OrderStatus.", ""),
                "notional": float(order.notional) if order.notional else None,
                "qty": float(order.qty) if order.qty else None,
                "submitted_at": str(getattr(order, "submitted_at", "")),
            }
        )

    trades = list(reversed(trade_log.all_rows()))[:40]

    return {
        "assets": assets,
        "portfolio": float(account.portfolio_value),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "account_status": str(account.status).split(".")[-1].title(),
        "total_pl": sum(float(p.unrealized_pl) for p in positions.values()),
        "updated": datetime.now().strftime("%H:%M:%S"),
        "orders": orders,
        "trades": trades,
    }


def gather_state() -> dict:
    env = read_env_settings()
    wifi_ip = lan_ip()
    state = {
        "app": {
            "version": read_version(),
            "visible_version": visible_version(),
        },
        "credentials": {
            "configured": credentials_configured(),
            "api_key_hint": mask_value(env["ALPACA_API_KEY"]),
            "base_url": env["ALPACA_BASE_URL"] or "https://paper-api.alpaca.markets",
        },
        "sharing": {
            "lan_ip": wifi_ip,
            "lan_url": f"http://{wifi_ip}:{ACTIVE_PORT}" if wifi_ip else None,
        },
        "service": get_service_status(),
        "watched_assets": load_bots(),
        "dashboard": None,
        "log_tail": tail_log(),
        "errors": [],
    }
    if state["credentials"]["configured"]:
        try:
            state["dashboard"] = fetch_data()
        except Exception as exc:
            state["errors"].append(str(exc))
    else:
        state["errors"].append("Add Alpaca paper credentials to unlock account data and asset validation.")
    return state


def json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200):
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def build_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trading Bot Control Room</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
  <style>
    :root { --bg:#111417; --panel:#181d20; --line:rgba(216,178,122,.18); --accent:#d8b27a; --accent2:#73b7ff; --text:#edf0eb; --muted:#93a09f; --good:#71d6ad; --bad:#ff8f70; --shadow:0 30px 60px rgba(0,0,0,.28); }
    * { box-sizing:border-box; }
    html,body { margin:0; min-height:100%; background:radial-gradient(circle at top left, rgba(216,178,122,.08), transparent 32%), linear-gradient(180deg, #0d1012 0%, #12171a 100%); color:var(--text); font-family:"Space Grotesk", sans-serif; overflow-x:hidden; }
    body { padding:24px; }
    button,input,select { font:inherit; }
    .shell { width:min(100%, 1520px); margin:0 auto; display:grid; grid-template-columns:minmax(260px, 300px) minmax(0,1fr); gap:20px; }
    .masthead { grid-column:1 / -1; display:flex; justify-content:space-between; align-items:center; gap:24px; padding-bottom:12px; border-bottom:1px solid var(--line); opacity:0; transform:translateY(10px); animation:rise .55s ease forwards; }
    .brand-kicker { color:var(--accent); text-transform:uppercase; letter-spacing:.18em; font-size:13px; font-weight:700; }
    .stat-sub { color:var(--muted); font-size:12px; margin-top:5px; }
    .top-actions,.btn-row,.service-tools { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    .status-pill { display:inline-flex; align-items:center; gap:8px; border:1px solid var(--line); padding:10px 14px; border-radius:999px; color:var(--muted); background:rgba(255,255,255,.02); transition:.2s ease; }
    .status-pill.running { color:var(--good); border-color:rgba(113,214,173,.35); }
    .status-pill.stopped { color:var(--bad); border-color:rgba(255,143,112,.28); }
    .status-pill.notice { color:var(--accent); }
    .sidebar,.workspace { min-width:0; }
    .rail { position:sticky; top:24px; display:grid; gap:18px; }
    .section { background:linear-gradient(180deg, rgba(255,255,255,.02), rgba(255,255,255,.01)); border:1px solid rgba(255,255,255,.05); border-radius:26px; padding:20px; box-shadow:var(--shadow); opacity:0; transform:translateY(14px); animation:rise .55s ease forwards; }
    .section:nth-child(2){animation-delay:.05s;} .section:nth-child(3){animation-delay:.1s;} .section:nth-child(4){animation-delay:.15s;}
    .section h2,.section h3 { margin:0; font-size:15px; letter-spacing:.01em; }
    .section-head { display:flex; justify-content:space-between; align-items:start; gap:14px; margin-bottom:14px; }
    details.section { padding:0; overflow:hidden; }
    details.section > summary { list-style:none; cursor:pointer; padding:20px; }
    details.section > summary::-webkit-details-marker { display:none; }
    details.section > summary .section-head { margin-bottom:0; }
    .collapsible-body { padding:0 20px 20px; border-top:1px solid rgba(255,255,255,.05); }
    .collapse-cue { color:var(--muted); font-size:12px; margin-left:auto; transition:transform .18s ease; }
    details[open] .collapse-cue { transform:rotate(180deg); }
    .section-note { color:var(--muted); font-size:13px; line-height:1.45; margin-top:6px; }
    .field { display:grid; gap:8px; margin-top:14px; }
    .field label { font-size:12px; text-transform:uppercase; letter-spacing:.12em; color:var(--muted); }
    .field-help { color:var(--muted); font-size:11px; line-height:1.4; margin-top:-2px; }
    input,select { width:100%; border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.03); color:var(--text); border-radius:14px; padding:12px 14px; outline:none; transition:.2s ease; }
    input:focus,select:focus { border-color:rgba(216,178,122,.55); background:rgba(255,255,255,.05); transform:translateY(-1px); }
    .field-row,.field-grid { display:grid; gap:10px; }
    .field-row { grid-template-columns:1fr 96px; align-items:end; }
    .field-grid.two { grid-template-columns:repeat(2,minmax(0,1fr)); }
    button { border:0; border-radius:999px; padding:12px 18px; cursor:pointer; transition:.18s ease; }
    button:hover { transform:translateY(-1px); }
    .primary { background:var(--accent); color:#171311; font-weight:700; }
    .secondary { background:rgba(255,255,255,.06); color:var(--text); border:1px solid rgba(255,255,255,.06); }
    .ghost { background:transparent; color:var(--muted); border:1px solid rgba(255,255,255,.08); }
    .danger { background:rgba(255,143,112,.16); color:var(--bad); border:1px solid rgba(255,143,112,.26); }
    .workspace { display:grid; gap:18px; }
    .stats-band { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; opacity:0; transform:translateY(14px); animation:rise .6s ease .08s forwards; }
    .stat { padding:16px 18px; border-top:1px solid rgba(255,255,255,.06); border-bottom:1px solid rgba(255,255,255,.06); background:linear-gradient(90deg, rgba(255,255,255,.03), rgba(255,255,255,.01)); border-radius:22px; }
    .stat-label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.12em; margin-bottom:12px; }
    .stat-value { font-size:clamp(24px,3vw,38px); line-height:1; letter-spacing:-.04em; }
    .workspace-grid { display:grid; grid-template-columns:minmax(0,1.75fr) minmax(260px, .8fr); gap:16px; align-items:start; }
    .charts { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:16px; }
    .chart-slab { min-height:310px; padding:18px; border-radius:24px; background:linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.015)); border:1px solid rgba(255,255,255,.06); overflow:hidden; }
    .chart-head { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; margin-bottom:10px; }
    .chart-symbol { font-size:20px; font-weight:700; letter-spacing:-.03em; }
    .chart-meta { color:var(--muted); font-size:12px; letter-spacing:.08em; text-transform:uppercase; }
    .chart-pl { margin-left:auto; font-weight:700; font-size:13px; }
    .chart-wrap { height:220px; position:relative; margin-top:14px; }
    .chart-line { position:absolute; left:0; right:130px; height:0; border-top-width:1px; border-top-style:dashed; pointer-events:none; }
    .chart-line.stop-line { border-top-color:#ff8f70; }
    .chart-line.trigger-line { border-top-color:#71d6ad; }
    .table-wrap { display:grid; gap:12px; margin-top:14px; }
    .tiny { color:var(--muted); font-size:12px; }
    .mono { font-family:"IBM Plex Mono", monospace; }
    .log-box { white-space:pre-wrap; font-family:"IBM Plex Mono", monospace; font-size:12px; line-height:1.55; color:#d3dbd6; background:rgba(0,0,0,.22); border-radius:18px; padding:14px; min-height:220px; max-height:420px; overflow:auto; border:1px solid rgba(255,255,255,.04); }
    .notice { font-size:13px; line-height:1.5; color:var(--muted); margin-top:10px; min-height:20px; transition:color .2s ease; }
    .notice.good { color:var(--good); } .notice.bad { color:var(--bad); } .notice.warn { color:var(--accent); }
    .empty { color:var(--muted); padding:28px 0 8px; line-height:1.5; }
    .table-shell { width:100%; overflow:auto; border-radius:18px; border:1px solid rgba(255,255,255,.05); background:rgba(0,0,0,.12); }
    table { width:100%; border-collapse:collapse; font-size:12px; table-layout:fixed; }
    th,td { padding:10px 12px; text-align:left; border-bottom:1px solid rgba(255,255,255,.05); }
    th { color:var(--muted); text-transform:uppercase; letter-spacing:.12em; font-size:11px; }
    td { color:var(--text); }
    tr:last-child td { border-bottom:0; }
    .badge { display:inline-flex; padding:4px 8px; border-radius:999px; font-size:11px; border:1px solid rgba(255,255,255,.08); }
    .badge.good { color:var(--good); border-color:rgba(113,214,173,.25); }
    .badge.bad { color:var(--bad); border-color:rgba(255,143,112,.25); }
    .badge.warn { color:var(--accent); border-color:rgba(216,178,122,.25); }
    .icon-btn { width:38px; height:38px; padding:0; display:inline-flex; align-items:center; justify-content:center; font-size:16px; }
    .line-chip { position:absolute; right:10px; transform:translateY(-50%); border-radius:999px; padding:3px 8px; font-size:11px; font-family:"IBM Plex Mono", monospace; pointer-events:none; }
    .stop-chip { background:#2d1a17; color:#ff8f70; border:1px solid rgba(255,143,112,.5); }
    .trigger-chip { background:#162b21; color:#71d6ad; border:1px solid rgba(113,214,173,.45); }
    @keyframes rise { to { opacity:1; transform:translateY(0); } }
    @media (max-width:1320px) { .workspace-grid { grid-template-columns:minmax(0,1fr) minmax(240px, 320px); } }
    @media (max-width:1200px) { .shell { grid-template-columns:1fr; } .rail { position:static; } .workspace-grid { grid-template-columns:1fr; } }
    @media (max-width:720px) { body { padding:16px; } .masthead { align-items:start; flex-direction:column; } .stats-band { grid-template-columns:repeat(2,minmax(0,1fr)); } .field-row,.field-grid.two { grid-template-columns:1fr; } .asset-row { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <div class="shell">
    <header class="masthead">
      <div class="brand-kicker">Trading Bot Control Room</div>
      <div class="top-actions">
        <div class="status-pill notice" id="versionBadge">v0.0</div>
        <div class="status-pill notice" id="globalStatus">Connecting…</div>
        <button class="ghost" id="shareBtn">Share</button>
        <button class="secondary" id="refreshBtn">Refresh Workspace</button>
      </div>
    </header>

    <aside class="sidebar">
      <div class="rail">
        <details class="section collapsible-section" id="credentialsSection">
          <summary>
            <div class="section-head">
              <div>
                <h2>Alpaca Credentials</h2>
              </div>
              <div style="display:flex;align-items:center;gap:8px">
                <span id="credsSummaryPill" class="badge"></span>
                <div class="collapse-cue">▾</div>
              </div>
            </div>
          </summary>
          <div class="collapsible-body">
          <form id="credentialsForm">
            <div class="field"><label for="apiKey">API key</label><input id="apiKey" name="api_key" autocomplete="off" placeholder="PK..." /></div>
            <div class="field"><label for="secretKey">Secret key</label><input id="secretKey" name="secret_key" autocomplete="off" placeholder="Paste your paper secret" /></div>
            <div class="field"><label for="baseUrl">Base URL</label><input id="baseUrl" name="base_url" value="https://paper-api.alpaca.markets" /></div>
            <div class="btn-row">
              <button class="primary" type="submit">Save Credentials</button>
              <button class="secondary" id="testCredsBtn" type="button">Test Connection</button>
              <a class="ghost" style="text-decoration:none;display:inline-flex;align-items:center;" href="https://app.alpaca.markets/paper/dashboard/overview" target="_blank" rel="noreferrer">Open Alpaca</a>
            </div>
            <div class="notice" id="credentialsNotice"></div>
          </form>
          </div>
        </details>

        <details class="section collapsible-section" id="serviceSection">
          <summary>
            <div class="section-head">
              <div>
                <h2>Bot Control</h2>
                <div class="section-note">Manage the launchd worker that runs <span class="mono">bot.py</span>.</div>
              </div>
              <div class="top-actions" style="gap:8px">
                <div class="status-pill" id="servicePill">Unknown</div>
                <div class="collapse-cue">▾</div>
              </div>
            </div>
          </summary>
          <div class="collapsible-body">
          <div class="btn-row">
            <button class="primary" id="startBtn" type="button">Start</button>
            <button class="secondary" id="reloadBtn" type="button">Reload</button>
            <button class="ghost" id="stopBtn" type="button">Stop</button>
          </div>
          <div class="service-tools" style="margin-top:10px">
            <button class="ghost" id="installAgentBtn" type="button">Install / Repair LaunchAgent</button>
          </div>
          <div class="notice" id="serviceNotice"></div>
          </div>
        </details>

        <details class="section collapsible-section" id="assetSection">
          <summary>
            <div class="section-head">
              <div>
                <h2>Assets</h2>
              </div>
              <div style="display:flex;align-items:center;gap:8px">
                <span id="assetsSummaryPill" class="badge"></span>
                <div class="collapse-cue">▾</div>
              </div>
            </div>
          </summary>
          <div class="collapsible-body">
          <h3 id="assetFormTitle" style="margin:0 0 14px;font-size:14px;letter-spacing:.01em;">Add Asset</h3>
          <form id="assetForm">
            <input type="hidden" id="assetOriginalSymbol" value="" />
            <div class="field"><label for="assetSymbol">Symbol</label><input id="assetSymbol" name="symbol" list="assetHints" placeholder="AAPL, Tesla, BTC/USD" autocomplete="off" /><datalist id="assetHints"></datalist><div class="field-help">Ticker or crypto pair, for example <span class="mono">AAPL</span> or <span class="mono">BTC/USD</span>.</div></div>
            <div class="field-grid two">
              <div class="field"><label for="assetInitial">Initial buy ($)</label><input id="assetInitial" name="initial_notional" placeholder="50" /><div class="field-help">Dollar amount for the first entry order.</div></div>
              <div class="field"><label for="assetLadder">Ladder buy ($)</label><input id="assetLadder" name="ladder_notional" placeholder="50" /><div class="field-help">Dollar amount for each additional buy on weakness.</div></div>
            </div>
            <div class="field"><label for="targetWeight">Target weight</label><input id="targetWeight" name="target_weight" value="0.20" /><div class="field-help">Portfolio weight used at rebalance. <span class="mono">0.50</span> means 50% of portfolio value.</div></div>
            <div class="field-grid two">
              <div class="field"><label for="stopPct">Stop multiplier (%)</label><input id="stopPct" name="stop_pct" value="0.95" /><div class="field-help">Exit floor as a fraction of entry. <span class="mono">0.95</span> means 95% of entry, or 5% below.</div></div>
              <div class="field"><label for="trailTrigger">Trail trigger (%)</label><input id="trailTrigger" name="trail_trigger" value="1.10" /><div class="field-help">Start trailing once price reaches this fraction of entry. <span class="mono">1.10</span> means +10%.</div></div>
            </div>
            <div class="field-grid two">
              <div class="field"><label for="trailStep">Trail step (%)</label><input id="trailStep" name="trail_step" value="1.05" /><div class="field-help">Re-raise the floor each time price gains this much from the last trigger. <span class="mono">1.05</span> means +5%.</div></div>
              <div class="field"><label for="trailStop">Trail floor (%)</label><input id="trailStop" name="trail_stop" value="0.95" /><div class="field-help">New stop floor after a trigger, as a fraction of current price. <span class="mono">0.95</span> means 5% below current.</div></div>
            </div>
            <div class="field-grid two">
              <div class="field"><label for="ladder1Pct">Ladder 1 (%)</label><input id="ladder1Pct" name="ladder1_pct" value="0.925" /><div class="field-help">First add level, measured off the current floor. <span class="mono">0.925</span> means 7.5% below the floor.</div></div>
              <div class="field"><label for="ladder2Pct">Ladder 2 (%)</label><input id="ladder2Pct" name="ladder2_pct" value="0.850" /><div class="field-help">Second add level, measured off the current floor. <span class="mono">0.850</span> means 15% below the floor.</div></div>
            </div>
            <div class="field"><label for="pollInterval">Poll interval (seconds)</label><input id="pollInterval" name="poll_interval" value="30" /><div class="field-help">How often the bot checks price and trading rules for this asset.</div></div>
            <div class="btn-row">
              <button class="primary" type="submit" id="assetSubmitBtn">Add Asset</button>
              <button class="ghost" type="button" id="assetResetBtn">Clear</button>
            </div>
            <div class="notice" id="assetNotice"></div>
          </form>
          </div>
        </details>
      </div>
    </aside>

    <main class="workspace">
      <section class="stats-band">
        <div class="stat"><div class="stat-label">Portfolio</div><div class="stat-value" id="portfolioValue">—</div></div>
        <div class="stat"><div class="stat-label">Cash</div><div class="stat-value" id="cashValue">—</div></div>
        <div class="stat"><div class="stat-label">Buying Power</div><div class="stat-value" id="buyingPowerValue">—</div></div>
        <div class="stat"><div class="stat-label">Unrealized P&amp;L</div><div class="stat-value" id="plValue">—</div><div class="stat-sub" id="plPct"></div></div>
      </section>

      <div class="workspace-grid">
        <details class="section collapsible-section" id="portfolioSection">
          <summary>
            <div class="section-head">
              <div><h3>Portfolio Dashboard</h3><div class="section-note" id="dashboardFreshness">Waiting for account data.</div></div>
              <div class="collapse-cue">▾</div>
            </div>
          </summary>
          <div class="collapsible-body">
          <div class="charts" id="chartsArea"></div>
          </div>
        </details>

        <div class="workspace" style="gap:18px">
          <details class="section collapsible-section" id="ordersSection">
            <summary>
              <div class="section-head">
                <div><h3>Open Orders</h3><div class="section-note">Live open orders from Alpaca.</div></div>
                <div class="collapse-cue">▾</div>
              </div>
            </summary>
            <div class="collapsible-body">
            <div class="table-shell"><table><thead><tr><th>Symbol</th><th>Side</th><th>Status</th><th>Amount</th></tr></thead><tbody id="ordersTable"></tbody></table></div>
            </div>
          </details>

          <details class="section collapsible-section" id="tradesSection">
            <summary>
              <div class="section-head">
                <div><h3>Trade History</h3><div class="section-note">Recent rows from <span class="mono">trades.tsv</span>.</div></div>
                <div class="collapse-cue">▾</div>
              </div>
            </summary>
            <div class="collapsible-body">
            <div class="table-shell"><table><thead><tr><th>Symbol</th><th>Side</th><th>Status</th><th>Notional</th><th>Submitted</th></tr></thead><tbody id="tradesTable"></tbody></table></div>
            </div>
          </details>

          <details class="section collapsible-section" id="logSection">
            <summary>
              <div class="section-head">
                <div><h3>Bot Log</h3><div class="section-note">Recent runtime output from <span class="mono">bot.log</span>.</div></div>
                <div class="collapse-cue">▾</div>
              </div>
            </summary>
            <div class="collapsible-body">
            <div class="log-box" id="logTail"></div>
            </div>
          </details>
        </div>
      </div>
    </main>
  </div>

  <script>
    const chartRegistry = new Map();

    function money(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(Number(value));
    }
    function signedMoney(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      const num = Number(value);
      return `${num >= 0 ? "+" : "-"}${money(Math.abs(num))}`;
    }
    function setNotice(id, message, tone="warn") {
      const el = document.getElementById(id);
      el.textContent = message || "";
      el.className = `notice ${tone}`;
    }
    async function request(url, options={}) {
      const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
      const payload = await response.json();
      if (!response.ok || payload.ok === false) throw new Error(payload.message || "Request failed");
      return payload;
    }
    function statusBadge(text, tone) {
      return `<span class="badge ${tone}">${text}</span>`;
    }
    function updateGlobalStatus(state) {
      const el = document.getElementById("globalStatus");
      const service = state.service || {};
      if (service.running) { el.className = "status-pill running"; el.textContent = `Bot running • ${service.label}`; }
      else if (service.available) { el.className = "status-pill stopped"; el.textContent = `Bot ${service.loaded ? "loaded" : "stopped"} • ${service.label}`; }
      else { el.className = "status-pill notice"; el.textContent = "LaunchAgent not installed"; }
    }
    function renderVersion(state) {
      const app = state.app || {};
      document.getElementById("versionBadge").textContent = app.visible_version || "v0.0";
    }
    function renderService(state) {
      const service = state.service || {};
      const pill = document.getElementById("servicePill");
      pill.textContent = service.detail || "Unknown";
      pill.className = "status-pill " + (service.running ? "running" : service.loaded ? "notice" : "stopped");
      setNotice("serviceNotice", service.available ? `${service.label} • ${service.program}` : `Expected LaunchAgent at ${service.path || "~/Library/LaunchAgents/com.trading.bot.plist"}`, service.available ? "warn" : "bad");
    }
    function renderSharing(state) {
      const btn = document.getElementById("shareBtn");
      const lanUrl = state.sharing && state.sharing.lan_url;
      btn.disabled = !lanUrl;
      btn.title = lanUrl ? `Share ${lanUrl}` : "LAN URL unavailable";
    }
    function renderCredentials(state) {
      const creds = state.credentials || {};
      document.getElementById("baseUrl").value = creds.base_url || "https://paper-api.alpaca.markets";
      document.getElementById("apiKey").placeholder = creds.api_key_hint ? `Saved: ${creds.api_key_hint}` : "PK...";
      document.getElementById("secretKey").placeholder = creds.configured ? "Secret already saved" : "Paste your paper secret";
      setNotice("credentialsNotice", creds.configured ? `Credentials saved for ${creds.api_key_hint || "paper account"}.` : "No Alpaca credentials saved yet.", creds.configured ? "good" : "warn");
      const pill = document.getElementById("credsSummaryPill");
      if (pill) { pill.textContent = creds.configured ? "Configured" : "Not set"; pill.className = "badge " + (creds.configured ? "good" : "warn"); }
    }
    function renderAssetsSummary(state) {
      const pill = document.getElementById("assetsSummaryPill");
      if (!pill) return;
      const count = (state.watched_assets || []).length;
      pill.textContent = `${count} asset${count !== 1 ? "s" : ""}`;
      pill.className = "badge " + (count > 0 ? "good" : "warn");
    }
    function renderStats(state) {
      const dash = state.dashboard;
      document.getElementById("portfolioValue").textContent = dash ? money(dash.portfolio) : "—";
      document.getElementById("cashValue").textContent = dash ? money(dash.cash) : "—";
      document.getElementById("buyingPowerValue").textContent = dash ? money(dash.buying_power) : "—";
      const plEl = document.getElementById("plValue");
      plEl.textContent = dash ? signedMoney(dash.total_pl) : "—";
      plEl.style.color = dash ? (Number(dash.total_pl) >= 0 ? "var(--good)" : "var(--bad)") : "var(--text)";
      const plPctEl = document.getElementById("plPct");
      if (plPctEl) {
        if (dash && dash.portfolio > 0) {
          const pct = (dash.total_pl / dash.portfolio * 100).toFixed(2);
          plPctEl.textContent = `${Number(pct) >= 0 ? "+" : ""}${pct}% of portfolio`;
        } else { plPctEl.textContent = ""; }
      }
      document.getElementById("dashboardFreshness").textContent = dash ? `Account ${dash.account_status} • updated ${dash.updated}` : (state.errors[0] || "Waiting for account data.");
    }
    function applyCollapseDefaults() {
      const mobile = window.matchMedia("(max-width: 720px)").matches;
      const openIds = mobile
        ? []
        : ["assetSection", "portfolioSection", "ordersSection", "tradesSection", "logSection"];
      document.querySelectorAll(".collapsible-section").forEach(section => {
        section.open = openIds.includes(section.id);
      });
    }
    function resetAssetForm() {
      document.getElementById("assetOriginalSymbol").value = "";
      document.getElementById("assetFormTitle").textContent = "Add Asset";
      document.getElementById("assetSubmitBtn").textContent = "Add Asset";
      document.getElementById("assetSymbol").value = "";
      document.getElementById("assetInitial").value = "";
      document.getElementById("assetLadder").value = "";
      document.getElementById("targetWeight").value = "0.20";
      document.getElementById("stopPct").value = "0.95";
      document.getElementById("trailTrigger").value = "1.10";
      document.getElementById("trailStep").value = "1.05";
      document.getElementById("trailStop").value = "0.95";
      document.getElementById("ladder1Pct").value = "0.925";
      document.getElementById("ladder2Pct").value = "0.850";
      document.getElementById("pollInterval").value = "30";
      setNotice("assetNotice", "");
    }
    function fillAssetForm(asset) {
      const assetSection = document.getElementById("assetSection");
      if (assetSection) assetSection.open = true;
      document.getElementById("assetOriginalSymbol").value = asset.symbol;
      document.getElementById("assetFormTitle").textContent = `Edit Asset • ${asset.symbol}`;
      document.getElementById("assetSubmitBtn").textContent = "Save Changes";
      document.getElementById("assetSymbol").value = asset.symbol;
      document.getElementById("assetInitial").value = asset.initial_notional;
      document.getElementById("assetLadder").value = asset.ladder_notional;
      document.getElementById("targetWeight").value = asset.target_weight;
      document.getElementById("stopPct").value = asset.stop_pct;
      document.getElementById("trailTrigger").value = asset.trail_trigger;
      document.getElementById("trailStep").value = asset.trail_step;
      document.getElementById("trailStop").value = asset.trail_stop;
      document.getElementById("ladder1Pct").value = asset.ladder1_pct;
      document.getElementById("ladder2Pct").value = asset.ladder2_pct;
      document.getElementById("pollInterval").value = asset.poll_interval;
      setNotice("assetNotice", `Editing ${asset.symbol}.`, "warn");
      const symbolField = document.getElementById("assetSymbol");
      if (assetSection) {
        assetSection.scrollIntoView({ behavior: "smooth", block: "start" });
      } else {
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
      setTimeout(() => {
        symbolField.focus();
        symbolField.select();
      }, 180);
    }
    function destroyCharts() { for (const chart of chartRegistry.values()) chart.destroy(); chartRegistry.clear(); }
    function renderCharts(state) {
      const root = document.getElementById("chartsArea");
      destroyCharts();
      root.innerHTML = "";
      const dash = state.dashboard;
      if (!dash || !dash.assets || !Object.keys(dash.assets).length) {
        root.innerHTML = '<div class="empty">Save credentials and start watching assets to see live portfolio charts here.</div>';
        return;
      }
      for (const [symbol, asset] of Object.entries(dash.assets)) {
        const slab = document.createElement("article");
        slab.className = "chart-slab";
        const safe = symbol.replaceAll("/", "_");
        const plTone = Number(asset.pl) >= 0 ? "var(--good)" : "var(--bad)";
        slab.innerHTML = `<div class="chart-head"><div class="chart-symbol">${symbol}</div><div class="chart-meta">${asset.asset_class}</div><div class="chart-pl" style="color:${plTone}">${signedMoney(asset.pl)} (${Number(asset.pl_pct || 0).toFixed(2)}%)</div></div><div class="section-note">Entry ${asset.entry ? money(asset.entry) : "—"} • Current ${money(asset.live)} • Qty ${Number(asset.qty || 0).toFixed(6)} • Value ${money(asset.mkt_val)}</div><div class="chart-wrap"><canvas id="chart_${safe}"></canvas></div>`;
        root.appendChild(slab);
        const entry = asset.entry || asset.live || 1;
        const allVals = [...(asset.prices || []), ...(asset.floors || [])];
        const half = allVals.length ? Math.max(...allVals.map(v => Math.abs(v - entry))) * 1.35 || entry * .1 : entry * .1;
        const stopValue = asset.floors && asset.floors.length ? Number(asset.floors[asset.floors.length - 1]) : (asset.entry ? Number(asset.entry) * 0.95 : null);
        const triggerValue = asset.entry ? Number(asset.entry) * Number(asset.trail_trigger || 1.1) : null;
        const yMin = entry - half;
        const yMax = entry + half;
        const overlay = slab.querySelector(".chart-wrap");
        function addLineChip(value, label, klass, lineClass) {
          if (value === null || value === undefined) return;
          const pct = Math.max(0, Math.min(100, ((yMax - value) / (yMax - yMin)) * 100));
          const line = document.createElement("div");
          line.className = `chart-line ${lineClass}`;
          line.style.top = `${pct}%`;
          overlay.appendChild(line);
          const chip = document.createElement("div");
          chip.className = `line-chip ${klass}`;
          chip.style.top = `${pct}%`;
          chip.textContent = `${label} ${money(value)}`;
          overlay.appendChild(chip);
        }
        addLineChip(stopValue, "Stop", "stop-chip", "stop-line");
        addLineChip(triggerValue, "Trigger", "trigger-chip", "trigger-line");
        const chart = new Chart(slab.querySelector("canvas"), {
          type: "line",
          data: { labels: asset.times, datasets: [
            { label: "Price", data: asset.prices, borderColor: asset.color, borderWidth: 2.2, pointRadius: 0, pointHoverRadius: 4, tension: .22 },
            { label: "Stop", data: asset.floors, borderColor: "#ff8f70", borderWidth: 1.4, borderDash: [6,5], pointRadius: 0, stepped: "before", tension: 0 },
            { label: "Trigger", data: (asset.times || []).map(() => triggerValue), borderColor: "#71d6ad", borderWidth: 1.2, borderDash: [3,4], pointRadius: 0, tension: 0 }
          ] },
          options: { animation: { duration: 220 }, responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, plugins: { legend: { display: false }, tooltip: { backgroundColor: "#111417", borderColor: "rgba(255,255,255,.08)", borderWidth: 1, titleColor: "#dfe8df", bodyColor: "#dfe8df", callbacks: { label: ctx => `${ctx.dataset.label}: ${money(ctx.parsed.y)}` } } }, scales: { x: { type: "time", time: { unit: "minute", displayFormats: { minute: "HH:mm" } }, ticks: { color: "#93a09f", maxTicksLimit: 6, maxRotation: 0 }, grid: { color: "rgba(255,255,255,.04)" } }, y: { min: entry - half, max: entry + half, ticks: { color: "#93a09f", maxTicksLimit: 5, callback: value => money(value) }, grid: { color: "rgba(255,255,255,.05)" } } } }
        });
        chartRegistry.set(symbol, chart);
      }
    }
    function renderOrders(state) {
      const root = document.getElementById("ordersTable");
      root.innerHTML = "";
      const orders = (state.dashboard && state.dashboard.orders) || [];
      if (!orders.length) { root.innerHTML = '<tr><td colspan="4" class="tiny">No open orders.</td></tr>'; return; }
      for (const order of orders) {
        const amount = order.notional ? money(order.notional) : order.qty;
        const tone = order.side === "BUY" ? "good" : "warn";
        root.innerHTML += `<tr><td>${order.symbol}</td><td>${statusBadge(order.side, tone)}</td><td>${order.status}</td><td>${amount}</td></tr>`;
      }
    }
    function renderTrades(state) {
      const root = document.getElementById("tradesTable");
      root.innerHTML = "";
      const trades = (state.dashboard && state.dashboard.trades) || [];
      if (!trades.length) { root.innerHTML = '<tr><td colspan="5" class="tiny">No trade history yet.</td></tr>'; return; }
      for (const trade of trades) {
        const tone = trade.status === "filled" ? "good" : trade.status === "cancelled" ? "bad" : "warn";
        root.innerHTML += `<tr><td>${trade.symbol}</td><td>${trade.side}</td><td>${statusBadge(trade.status, tone)}</td><td>${money(trade.notional || 0)}</td><td>${trade.submitted_at || "—"}</td></tr>`;
      }
    }
    function renderLog(state) { document.getElementById("logTail").textContent = state.log_tail || "No bot log yet."; }
    async function refreshState() {
      try {
        const payload = await request("/api/state");
        const state = payload.state;
        renderVersion(state);
        updateGlobalStatus(state);
        renderService(state);
        renderSharing(state);
        renderCredentials(state);
        renderStats(state);
        renderCharts(state);
        renderOrders(state);
        renderTrades(state);
        renderLog(state);
        renderAssetsSummary(state);
      } catch (error) {
        updateGlobalStatus({ service: { available: false } });
        setNotice("serviceNotice", error.message, "bad");
      }
    }
    async function postAndRefresh(url, body, noticeId, successTone="good", method="POST") {
      try {
        const payload = await request(url, { method, body: JSON.stringify(body) });
        setNotice(noticeId, payload.message, successTone);
        await refreshState();
      } catch (error) {
        setNotice(noticeId, error.message, "bad");
      }
    }
    document.getElementById("refreshBtn").addEventListener("click", refreshState);
    document.getElementById("shareBtn").addEventListener("click", async () => {
      try {
        const payload = await request("/api/state");
        const url = payload.state.sharing && payload.state.sharing.lan_url;
        if (!url) throw new Error("LAN URL unavailable");
        if (navigator.share) {
          await navigator.share({ title: "Trading Bot Control Room", url });
        } else if (navigator.clipboard) {
          await navigator.clipboard.writeText(url);
          setNotice("serviceNotice", `Copied ${url}`, "good");
        } else {
          window.prompt("Copy this URL", url);
        }
      } catch (error) {
        setNotice("serviceNotice", error.message || "Could not share link.", "bad");
      }
    });
    document.getElementById("credentialsForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      await postAndRefresh("/api/credentials", { api_key: form.get("api_key") || "", secret_key: form.get("secret_key") || "", base_url: form.get("base_url") || "" }, "credentialsNotice");
      document.getElementById("secretKey").value = "";
    });
    document.getElementById("testCredsBtn").addEventListener("click", async () => {
      try {
        const payload = await request("/api/credentials/test", { method: "POST", body: "{}" });
        setNotice("credentialsNotice", `Connected: ${payload.result.account_status} • Portfolio ${money(payload.result.portfolio_value)} • Buying power ${money(payload.result.buying_power)}`, "good");
        await refreshState();
      } catch (error) {
        setNotice("credentialsNotice", error.message, "bad");
      }
    });
    document.getElementById("assetForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const symbol = document.getElementById("assetOriginalSymbol").value;
      const form = new FormData(event.currentTarget);
      const body = Object.fromEntries(form.entries());
      if (symbol) await postAndRefresh(`/api/assets/${encodeURIComponent(symbol)}`, body, "assetNotice", "good", "PUT");
      else await postAndRefresh("/api/assets", body, "assetNotice");
      if (!symbol) event.currentTarget.reset();
    });
    document.getElementById("assetResetBtn").addEventListener("click", resetAssetForm);
    document.getElementById("startBtn").addEventListener("click", () => postAndRefresh("/api/service", { action: "start" }, "serviceNotice"));
    document.getElementById("stopBtn").addEventListener("click", () => postAndRefresh("/api/service", { action: "stop" }, "serviceNotice", "warn"));
    document.getElementById("reloadBtn").addEventListener("click", () => postAndRefresh("/api/service", { action: "reload" }, "serviceNotice"));
    document.getElementById("installAgentBtn").addEventListener("click", () => postAndRefresh("/api/service", { action: "install" }, "serviceNotice"));
    let searchTimer;
    document.getElementById("assetSymbol").addEventListener("input", (event) => {
      clearTimeout(searchTimer);
      const q = event.target.value.trim();
      if (q.length < 2) return;
      searchTimer = setTimeout(async () => {
        try {
          const payload = await request(`/api/search-assets?q=${encodeURIComponent(q)}`);
          const datalist = document.getElementById("assetHints");
          datalist.innerHTML = "";
          for (const item of payload.results || []) {
            const option = document.createElement("option");
            option.value = item.symbol;
            option.label = item.name;
            datalist.appendChild(option);
          }
        } catch (_error) {}
      }, 180);
    });
    resetAssetForm();
    applyCollapseDefaults();
    refreshState();
    setInterval(refreshState, 20000);
  </script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode() or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = build_html().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/state":
            return json_response(self, {"ok": True, "state": gather_state()})
        if parsed.path == "/api/search-assets":
            query = parse_qs(parsed.query).get("q", [""])[0]
            try:
                results = [{"symbol": sym, "name": name} for sym, name in search_assets(query)]
                return json_response(self, {"ok": True, "results": results})
            except Exception as exc:
                return json_response(self, {"ok": False, "message": str(exc)}, 400)
        return json_response(self, {"ok": False, "message": "Not found"}, 404)

    def do_POST(self):
        try:
            payload = self._read_json()
            if self.path == "/api/credentials":
                save_env_settings(payload.get("api_key", ""), payload.get("secret_key", ""), payload.get("base_url", ""))
                return json_response(self, {"ok": True, "message": "Credentials saved."})
            if self.path == "/api/credentials/test":
                return json_response(self, {"ok": True, "message": "Credentials verified.", "result": test_credentials()})
            if self.path == "/api/assets":
                return json_response(self, {"ok": True, "message": add_asset_from_request(payload)})
            if self.path == "/api/service":
                action = payload.get("action")
                if action == "start":
                    message = start_service()
                elif action == "stop":
                    message = stop_service()
                elif action == "reload":
                    message = reload_service()
                elif action == "install":
                    install_message = install_or_repair_launch_agent()
                    message = f"{install_message} {reload_service()}" if get_service_status()["loaded"] else install_message
                else:
                    raise RuntimeError("Unknown service action.")
                return json_response(self, {"ok": True, "message": message})
        except Exception as exc:
            return json_response(self, {"ok": False, "message": str(exc)}, 400)
        return json_response(self, {"ok": False, "message": "Not found"}, 404)

    def do_PUT(self):
        try:
            payload = self._read_json()
            if self.path.startswith("/api/assets/"):
                symbol = self.path.split("/api/assets/", 1)[1]
                return json_response(self, {"ok": True, "message": update_asset_from_request(symbol, payload)})
        except Exception as exc:
            return json_response(self, {"ok": False, "message": str(exc)}, 400)
        return json_response(self, {"ok": False, "message": "Not found"}, 404)

    def do_DELETE(self):
        try:
            if self.path.startswith("/api/assets/"):
                symbol = self.path.split("/api/assets/", 1)[1]
                return json_response(self, {"ok": True, "message": remove_asset(symbol)})
        except Exception as exc:
            return json_response(self, {"ok": False, "message": str(exc)}, 400)
        return json_response(self, {"ok": False, "message": "Not found"}, 404)


def main():
    global ACTIVE_PORT
    port = PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 >= len(sys.argv):
            raise SystemExit("--port requires a value")
        port = int(sys.argv[idx + 1])
    ACTIVE_PORT = port
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Control panel -> http://localhost:{port}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.4, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
