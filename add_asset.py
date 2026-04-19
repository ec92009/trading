"""
TUI for adding a new asset to the trading bot.
Shows current cash availability, takes symbol + investment (or company name),
writes a new BotConfig entry to bot.py, and reloads the service.
"""

import os
import re
import subprocess
import threading
from pathlib import Path

from alpaca_env import load_alpaca_credentials
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Header, Input, Button, Label, Static, Rule
from textual import on, work

HERE = Path(__file__).parent
ALPACA = load_alpaca_credentials()

# ── Alpaca helpers ────────────────────────────────────────────────────────────

def fetch_account():
    from alpaca.trading.client import TradingClient
    c = TradingClient(
        api_key=ALPACA["api_key"],
        secret_key=ALPACA["secret_key"],
        paper=True,
    )
    a = c.get_account()
    return float(a.cash), float(a.buying_power), float(a.portfolio_value)

def existing_symbols():
    text = (HERE / "bot.py").read_text()
    return re.findall(r'symbol\s*=\s*"([^"]+)"', text)

def validate_symbol(symbol: str, asset_class: str) -> str | None:
    """
    Confirm the symbol is tradable on Alpaca.
    Returns None if valid, or an error string if not.
    """
    from alpaca.trading.client import TradingClient
    from alpaca.trading.exceptions import APIError
    c = TradingClient(
        api_key=ALPACA["api_key"],
        secret_key=ALPACA["secret_key"],
        paper=True,
    )
    try:
        if asset_class == "crypto":
            from alpaca.data.historical import CryptoHistoricalDataClient
            from alpaca.data.requests import CryptoLatestQuoteRequest
            data = CryptoHistoricalDataClient(
                api_key=ALPACA["api_key"],
                secret_key=ALPACA["secret_key"],
            )
            quote = data.get_crypto_latest_quote(
                CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
            )
            if symbol not in quote:
                return f"{symbol} not found on Alpaca."
            return None
        asset = c.get_asset(symbol)
        if not asset.tradable:
            return f"{symbol} exists but is not tradable."
        return None
    except APIError:
        return f"{symbol} not found on Alpaca."
    except Exception as e:
        return f"Could not validate {symbol}: {e}"

def add_to_bot(symbol: str, asset_class: str, target_weight: float):
    path = HERE / "bot.py"
    text = path.read_text()
    new_line = (
        f'    BotConfig(symbol="{symbol}", '
        f'asset_class="{asset_class}", '
        f'target_weight={target_weight:.4f}),\n'
    )
    pattern = r'(BOTS\s*=\s*\[.*?)(^\])'
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError("Could not find BOTS list in bot.py")
    insert_pos = match.start(2)
    path.write_text(text[:insert_pos] + new_line + text[insert_pos:])

def reload_service():
    uid   = subprocess.check_output(["id", "-u"]).decode().strip()
    plist = Path.home() / "Library/LaunchAgents/com.trading.bot.plist"
    bootout = subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(plist)],
        capture_output=True,
        text=True,
    )
    load = subprocess.run(
        ["launchctl", "load", str(plist)],
        capture_output=True,
        text=True,
    )
    if load.returncode != 0:
        detail = (load.stderr or load.stdout or "").strip()
        raise RuntimeError(f"launchctl load failed: {detail or 'unknown error'}")
    return bootout, load

# ── Asset search ──────────────────────────────────────────────────────────────

_asset_cache: list[dict] = []
_cache_lock  = threading.Lock()

def _ensure_cache():
    """Lazy-load all active US equity assets from Alpaca into memory."""
    global _asset_cache
    with _cache_lock:
        if _asset_cache:
            return
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetAssetsRequest
        from alpaca.trading.enums import AssetClass, AssetStatus
        c = TradingClient(
            api_key=ALPACA["api_key"],
            secret_key=ALPACA["secret_key"],
            paper=True,
        )
        assets = c.get_all_assets(GetAssetsRequest(
            asset_class=AssetClass.US_EQUITY,
            status=AssetStatus.ACTIVE,
        ))
        _asset_cache = [
            {"symbol": a.symbol, "name": a.name or ""}
            for a in assets if a.tradable
        ]

def search_assets(query: str) -> list[tuple[str, str]]:
    """
    Return up to 5 (symbol, name) matches for a name or partial-symbol query.
    Prioritises: exact symbol match → symbol prefix → name starts with → name contains.
    """
    _ensure_cache()
    q = query.lower().strip()
    if not q:
        return []

    buckets: list[list[tuple[str, str]]] = [[], [], [], []]
    for a in _asset_cache:
        sym  = a["symbol"].lower()
        name = a["name"].lower()
        entry = (a["symbol"], a["name"])
        if sym == q:
            buckets[0].append(entry)
        elif sym.startswith(q):
            buckets[1].append(entry)
        elif name.startswith(q):
            buckets[2].append(entry)
        elif q in name:
            buckets[3].append(entry)

    results: list[tuple[str, str]] = []
    for bucket in buckets:
        results.extend(sorted(bucket, key=lambda x: len(x[1])))
        if len(results) >= 5:
            break
    return results[:5]

# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
Screen {
    background: #0f1117;
    align: center top;
}

#card {
    width: 64;
    height: auto;
    background: #1a1d27;
    border: solid #2a2d3a;
    padding: 1 2;
    margin: 2 0;
}

#title {
    text-align: center;
    text-style: bold;
    color: #ffffff;
    margin-bottom: 1;
}

.section-label {
    color: #888888;
    margin-top: 1;
}

.money {
    color: #2ecc71;
    text-style: bold;
}

.row {
    height: 3;
    margin-top: 1;
}

.field-label {
    width: 18;
    color: #aaaaaa;
    content-align: left middle;
    padding-top: 1;
}

Input {
    width: 1fr;
    background: #0f1117;
    border: solid #2a2d3a;
    color: #ffffff;
}

Input:focus {
    border: solid #4A90D9;
}

/* Suggestion buttons */
#suggestions {
    display: none;
    margin-left: 18;
    margin-top: 0;
    height: auto;
}

.sug-btn {
    width: 1fr;
    height: 1;
    background: #0f1117;
    border: none;
    color: #aaaaaa;
    text-align: left;
    padding: 0 1;
    margin-bottom: 0;
}

.sug-btn:hover {
    background: #1a3a5c;
    color: #4A90D9;
}

#search-status {
    color: #555;
    height: 1;
    margin-left: 18;
    margin-top: 0;
}

#mode-btn {
    width: 6;
    min-width: 6;
    margin-left: 1;
    background: #2a2d3a;
    color: #aaaaaa;
    border: solid #3a3d4a;
}

#mode-btn.active-dollar {
    background: #1a5276;
    color: #4A90D9;
    border: solid #4A90D9;
}

#mode-btn.active-percent {
    background: #1a5276;
    color: #4A90D9;
    border: solid #4A90D9;
}

#computed {
    color: #888888;
    height: 1;
    margin-left: 19;
    margin-top: 0;
}

#error-msg {
    color: #e74c3c;
    height: 1;
    margin-top: 1;
    text-align: center;
}

#success-msg {
    color: #2ecc71;
    text-style: bold;
    height: 1;
    margin-top: 1;
    text-align: center;
}

#btn-row {
    margin-top: 2;
    height: 3;
    align: center middle;
}

#add-btn {
    background: #1a5276;
    color: #4A90D9;
    border: solid #4A90D9;
    margin-right: 2;
    min-width: 16;
}

#add-btn:hover {
    background: #4A90D9;
    color: #ffffff;
}

#another-btn {
    display: none;
    background: #1a5276;
    color: #4A90D9;
    border: solid #4A90D9;
    margin-right: 2;
    min-width: 16;
}

#another-btn:hover {
    background: #4A90D9;
    color: #ffffff;
}

#cancel-btn {
    background: #2a2d3a;
    color: #888888;
    border: solid #3a3d4a;
    min-width: 16;
}

#cancel-btn:hover {
    background: #c0392b;
    color: #ffffff;
    border: solid #c0392b;
}

Rule {
    color: #2a2d3a;
    margin: 1 0;
}
"""

# ── App ───────────────────────────────────────────────────────────────────────

MAX_SUGGESTIONS = 5

class AddAssetApp(App):
    CSS = CSS
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+c", "cancel", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self._cash          = 0.0
        self._buying_power  = 0.0
        self._portfolio     = 0.0
        self._mode          = "$"
        self._suggestions: list[tuple[str, str]] = []

    def on_mount(self):
        self.title = "Trading Bot"
        try:
            self._cash, self._buying_power, self._portfolio = fetch_account()
            self.query_one("#cash-val").update(f"${self._cash:,.2f}")
            self.query_one("#bp-val").update(f"${self._buying_power:,.2f}")
            self.query_one("#port-val").update(f"${self._portfolio:,.2f}")
        except Exception as e:
            self.query_one("#error-msg").update(f"Could not load account: {e}")
        # Pre-warm asset cache in background
        self.run_worker(self._warm_cache, thread=True)

    def _warm_cache(self):
        _ensure_cache()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="card"):
            yield Label("Add New Asset", id="title")
            yield Rule()

            # Account summary
            yield Label("Account", classes="section-label")
            with Horizontal(classes="row"):
                yield Label("Portfolio value", classes="field-label")
                yield Static("—", id="port-val", classes="money")
            with Horizontal(classes="row"):
                yield Label("Cash", classes="field-label")
                yield Static("—", id="cash-val", classes="money")
            with Horizontal(classes="row"):
                yield Label("Buying power", classes="field-label")
                yield Static("—", id="bp-val", classes="money")

            yield Rule()

            # Symbol input + suggestions
            yield Label("Asset", classes="section-label")
            with Horizontal(classes="row"):
                yield Label("Symbol / Name", classes="field-label")
                yield Input(placeholder="AAPL, Tesla, BTC/USD…", id="symbol-input")

            yield Static("", id="search-status")

            # Suggestion buttons (hidden until results arrive)
            with Container(id="suggestions"):
                for i in range(MAX_SUGGESTIONS):
                    yield Button("", id=f"sug-{i}", classes="sug-btn")

            # Investment input
            with Horizontal(classes="row"):
                yield Label("Investment", classes="field-label")
                yield Input(placeholder="50", id="amount-input")
                yield Button("$", id="mode-btn", classes="active-dollar")

            yield Static("", id="computed")
            yield Label("", id="error-msg")
            yield Label("", id="success-msg")

            with Horizontal(id="btn-row"):
                yield Button("Add Asset",   id="add-btn",     variant="primary")
                yield Button("Add Another", id="another-btn", variant="primary")
                yield Button("Cancel",      id="cancel-btn")

        yield Footer()

    # ── Symbol search ─────────────────────────────────────────────────────────

    @on(Input.Changed, "#symbol-input")
    def symbol_changed(self, event: Input.Changed):
        query = event.value.strip()
        self._hide_suggestions()
        self.query_one("#error-msg").update("")

        # Already looks like a ticker (all-caps, ≤6 chars, no spaces) → skip search
        if re.match(r'^[A-Z]{1,6}(/[A-Z]{1,6})?$', query):
            self.query_one("#search-status").update("")
            return

        if len(query) < 2:
            self.query_one("#search-status").update("")
            return

        self.query_one("#search-status").update("searching…")
        self.run_worker(lambda: self._do_search(query), thread=True, exclusive=True)

    def _do_search(self, query: str):
        results = search_assets(query)
        self.call_from_thread(self._show_suggestions, query, results)

    def _show_suggestions(self, query: str, results: list[tuple[str, str]]):
        # Guard: only show if input still matches
        current = self.query_one("#symbol-input").value.strip()
        if current.lower() != query.lower():
            return

        self._suggestions = results
        self.query_one("#search-status").update("")

        if not results:
            self.query_one("#search-status").update(f'No results for "{query}"')
            return

        sug_box = self.query_one("#suggestions")
        sug_box.display = True
        for i, btn in enumerate(sug_box.query(".sug-btn")):
            if i < len(results):
                sym, name = results[i]
                btn.label   = f"  {sym}  —  {name}"
                btn.display = True
            else:
                btn.display = False

    def _hide_suggestions(self):
        self.query_one("#suggestions").display = False
        self._suggestions = []

    @on(Button.Pressed, ".sug-btn")
    def pick_suggestion(self, event: Button.Pressed):
        idx = int(event.button.id.split("-")[1])
        if idx < len(self._suggestions):
            sym, name = self._suggestions[idx]
            self.query_one("#symbol-input").value = sym
            self._hide_suggestions()
            self.query_one("#search-status").update(f"✓ {name}")
            self.query_one("#amount-input").focus()

    # ── Mode toggle ───────────────────────────────────────────────────────────

    @on(Button.Pressed, "#mode-btn")
    def toggle_mode(self):
        self._mode = "%" if self._mode == "$" else "$"
        btn = self.query_one("#mode-btn")
        btn.label = self._mode
        btn.remove_class("active-dollar", "active-percent")
        btn.add_class("active-dollar" if self._mode == "$" else "active-percent")
        self._update_computed()

    @on(Input.Changed, "#amount-input")
    def amount_changed(self):
        self._update_computed()

    def _update_computed(self):
        raw = self.query_one("#amount-input").value.strip()
        try:
            val = float(raw)
            if self._mode == "%":
                dollars = self._buying_power * val / 100
                pct     = val
            else:
                dollars = val
                pct     = (val / self._buying_power * 100) if self._buying_power else 0
            self.query_one("#computed").update(
                f"= ${dollars:,.2f}  ({pct:.1f}% of buying power)"
            )
        except ValueError:
            self.query_one("#computed").update("")

    # ── Add ───────────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#add-btn")
    def do_add(self):
        self.query_one("#error-msg").update("")
        self.query_one("#success-msg").update("")

        symbol = self.query_one("#symbol-input").value.strip().upper()
        raw    = self.query_one("#amount-input").value.strip()

        if not symbol:
            self._error("Symbol is required.")
            return
        if symbol in [s.upper() for s in existing_symbols()]:
            self._error(f"{symbol} is already in the bot.")
            return
        try:
            val = float(raw)
            if val <= 0:
                raise ValueError
        except ValueError:
            self._error("Enter a positive number for investment amount.")
            return

        notional = round(
            self._buying_power * val / 100 if self._mode == "%" else val, 2
        )
        if notional > self._buying_power:
            self._error(
                f"${notional:,.2f} exceeds buying power (${self._buying_power:,.2f})."
            )
            return

        asset_class = "crypto" if "/" in symbol else "stock"

        # Validate symbol against Alpaca before writing anything
        err = validate_symbol(symbol, asset_class)
        if err:
            self._error(err)
            return

        try:
            target_weight = round(notional / self._portfolio, 4) if self._portfolio > 0 else 1.0
            add_to_bot(symbol, asset_class, target_weight)
            reload_service()
            self._buying_power -= notional
            self.query_one("#bp-val").update(f"${self._buying_power:,.2f}")
            self.query_one("#success-msg").update(
                f"✓ {symbol} added — target {target_weight:.2%} (~${notional:,.2f}, {asset_class}) — bot reloaded."
            )
            self.query_one("#add-btn").display     = False
            self.query_one("#another-btn").display = True
            self.query_one("#cancel-btn").label    = "Done"
            self.query_one("#symbol-input").disabled = True
            self.query_one("#amount-input").disabled = True
            self.query_one("#mode-btn").disabled     = True
        except Exception as e:
            self._error(str(e))

    def _error(self, msg: str):
        self.query_one("#error-msg").update(msg)

    # ── Add another ───────────────────────────────────────────────────────────

    @on(Button.Pressed, "#another-btn")
    def do_another(self):
        self.query_one("#symbol-input").value    = ""
        self.query_one("#amount-input").value    = ""
        self.query_one("#symbol-input").disabled = False
        self.query_one("#amount-input").disabled = False
        self.query_one("#mode-btn").disabled     = False
        self.query_one("#success-msg").update("")
        self.query_one("#error-msg").update("")
        self.query_one("#search-status").update("")
        self.query_one("#computed").update("")
        self.query_one("#add-btn").display       = True
        self.query_one("#another-btn").display   = False
        self.query_one("#cancel-btn").label      = "Cancel"
        self._hide_suggestions()
        self.query_one("#symbol-input").focus()

    @on(Button.Pressed, "#cancel-btn")
    def action_cancel(self):
        self.exit()


if __name__ == "__main__":
    AddAssetApp().run()
