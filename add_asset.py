"""
TUI for adding a new asset to the trading bot.
Shows current cash availability, takes symbol + investment,
writes a new BotConfig entry to bot.py, and reloads the service.
"""

import os
import re
import subprocess
from pathlib import Path
from dotenv import load_dotenv

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Button, Label, Static, Rule
from textual import on

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

# ── Alpaca ────────────────────────────────────────────────────────────────────

def fetch_account():
    from alpaca.trading.client import TradingClient
    c = TradingClient(
        api_key=os.getenv("ALPACA_API_KEY"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
        paper=True,
    )
    a = c.get_account()
    return float(a.cash), float(a.buying_power), float(a.portfolio_value)

def existing_symbols():
    """Return symbols already in the BOTS list."""
    text = (HERE / "bot.py").read_text()
    return re.findall(r'symbol\s*=\s*"([^"]+)"', text)

def add_to_bot(symbol: str, asset_class: str, notional: float):
    """Append a new BotConfig entry to the BOTS list in bot.py."""
    path = HERE / "bot.py"
    text = path.read_text()

    new_line = (
        f'    BotConfig(symbol="{symbol}", '
        f'asset_class="{asset_class}", '
        f'initial_notional={notional:.2f}, '
        f'ladder_notional={notional:.2f}),\n'
    )

    # Insert before the closing ] of the BOTS list
    pattern = r'(BOTS\s*=\s*\[.*?)(^\])'
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError("Could not find BOTS list in bot.py")

    insert_pos = match.start(2)
    text = text[:insert_pos] + new_line + text[insert_pos:]
    path.write_text(text)

def reload_service():
    uid = subprocess.check_output(["id", "-u"]).decode().strip()
    plist = Path.home() / "Library/LaunchAgents/com.trading.bot.plist"
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)],
                   capture_output=True)
    subprocess.run(["launchctl", "load", str(plist)], capture_output=True)

# ── TUI ───────────────────────────────────────────────────────────────────────

CSS = """
Screen {
    background: #0f1117;
}

#card {
    width: 60;
    height: auto;
    background: #1a1d27;
    border: solid #2a2d3a;
    padding: 1 2;
    margin: 2 auto;
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

Input.-invalid {
    border: solid #e74c3c;
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
    height: 2;
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

class AddAssetApp(App):
    CSS = CSS
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+c", "cancel", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self._cash = 0.0
        self._buying_power = 0.0
        self._portfolio = 0.0
        self._mode = "$"   # "$" or "%"

    def on_mount(self):
        self.title = "Trading Bot"
        try:
            self._cash, self._buying_power, self._portfolio = fetch_account()
            self.query_one("#cash-val").update(f"${self._cash:,.2f}")
            self.query_one("#bp-val").update(f"${self._buying_power:,.2f}")
            self.query_one("#port-val").update(f"${self._portfolio:,.2f}")
        except Exception as e:
            self.query_one("#error-msg").update(f"Could not load account: {e}")

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

            # Symbol input
            yield Label("Asset", classes="section-label")
            with Horizontal(classes="row"):
                yield Label("Symbol", classes="field-label")
                yield Input(placeholder="AAPL, BTC/USD, NVDA…", id="symbol-input")

            # Investment input
            with Horizontal(classes="row"):
                yield Label("Investment", classes="field-label")
                yield Input(placeholder="50", id="amount-input")
                yield Button("$", id="mode-btn", classes="active-dollar")

            yield Static("", id="computed")

            yield Label("", id="error-msg")
            yield Label("", id="success-msg")

            with Horizontal(id="btn-row"):
                yield Button("Add Asset", id="add-btn", variant="primary")
                yield Button("Cancel", id="cancel-btn")

        yield Footer()

    # ── Interactivity ─────────────────────────────────────────────────────────

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

    @on(Button.Pressed, "#add-btn")
    def do_add(self):
        self.query_one("#error-msg").update("")
        self.query_one("#success-msg").update("")

        symbol = self.query_one("#symbol-input").value.strip().upper()
        raw    = self.query_one("#amount-input").value.strip()

        # Validate
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

        if self._mode == "%":
            notional = round(self._buying_power * val / 100, 2)
        else:
            notional = round(val, 2)

        if notional > self._buying_power:
            self._error(
                f"${notional:,.2f} exceeds buying power (${self._buying_power:,.2f})."
            )
            return

        # Detect asset class
        asset_class = "crypto" if "/" in symbol else "stock"

        try:
            add_to_bot(symbol, asset_class, notional)
            reload_service()
            self.query_one("#success-msg").update(
                f"✓ {symbol} added (${notional:,.2f}, {asset_class}).\n"
                f"  Bot reloaded."
            )
            self.query_one("#add-btn").disabled = True
            self.query_one("#symbol-input").disabled = True
            self.query_one("#amount-input").disabled = True
        except Exception as e:
            self._error(str(e))

    def _error(self, msg: str):
        self.query_one("#error-msg").update(msg)

    @on(Button.Pressed, "#cancel-btn")
    def action_cancel(self):
        self.exit()


if __name__ == "__main__":
    AddAssetApp().run()
