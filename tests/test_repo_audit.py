import importlib
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


def install_alpaca_stubs():
    alpaca = ModuleType("alpaca")
    trading = ModuleType("alpaca.trading")
    trading_client = ModuleType("alpaca.trading.client")
    trading_requests = ModuleType("alpaca.trading.requests")
    trading_enums = ModuleType("alpaca.trading.enums")
    trading_exceptions = ModuleType("alpaca.trading.exceptions")
    data = ModuleType("alpaca.data")
    data_historical = ModuleType("alpaca.data.historical")
    data_requests = ModuleType("alpaca.data.requests")

    class TradingClient:
        def __init__(self, *args, **kwargs):
            pass

    class StockHistoricalDataClient:
        def __init__(self, *args, **kwargs):
            pass

    class CryptoHistoricalDataClient:
        def __init__(self, *args, **kwargs):
            pass

    class MarketOrderRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class GetOrdersRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class StockLatestQuoteRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class CryptoLatestQuoteRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class APIError(Exception):
        pass

    trading_client.TradingClient = TradingClient
    trading_requests.MarketOrderRequest = MarketOrderRequest
    trading_requests.GetOrdersRequest = GetOrdersRequest
    trading_enums.OrderSide = SimpleNamespace(BUY="BUY", SELL="SELL")
    trading_enums.TimeInForce = SimpleNamespace(GTC="GTC", DAY="DAY")
    trading_enums.QueryOrderStatus = SimpleNamespace(OPEN="OPEN")
    trading_exceptions.APIError = APIError
    data_historical.StockHistoricalDataClient = StockHistoricalDataClient
    data_historical.CryptoHistoricalDataClient = CryptoHistoricalDataClient
    data_requests.StockLatestQuoteRequest = StockLatestQuoteRequest
    data_requests.CryptoLatestQuoteRequest = CryptoLatestQuoteRequest

    modules = {
        "alpaca": alpaca,
        "alpaca.trading": trading,
        "alpaca.trading.client": trading_client,
        "alpaca.trading.requests": trading_requests,
        "alpaca.trading.enums": trading_enums,
        "alpaca.trading.exceptions": trading_exceptions,
        "alpaca.data": data,
        "alpaca.data.historical": data_historical,
        "alpaca.data.requests": data_requests,
    }
    return patch.dict(sys.modules, modules)


class BotBehaviorTests(unittest.TestCase):
    def test_buy_does_not_guess_position_size(self):
        with install_alpaca_stubs():
            bot = importlib.import_module("bot")
            importlib.reload(bot)

            trade_log = SimpleNamespace(log_order=Mock())
            fake_trading = SimpleNamespace(
                get_account=Mock(return_value=SimpleNamespace(buying_power="1000")),
                submit_order=Mock(return_value=SimpleNamespace(status="accepted", id="ord-1")),
            )

            with patch.object(bot, "trade_log", trade_log), patch.object(bot, "trading", fake_trading):
                b = bot.Bot(bot.BotConfig(symbol="AAPL", asset_class="stock"))
                b.entry_price = 100.0
                b.total_qty = 1.5
                b.buy(50.0, "test")
                self.assertEqual(b.total_qty, 1.5)
                trade_log.log_order.assert_called_once()

    def test_sell_all_uses_live_position_qty(self):
        with install_alpaca_stubs():
            bot = importlib.import_module("bot")
            importlib.reload(bot)

            trade_log = SimpleNamespace(log_order=Mock())
            live_pos = SimpleNamespace(qty="2.25", avg_entry_price="101.5")
            fake_trading = SimpleNamespace(
                get_all_positions=Mock(return_value=[SimpleNamespace(symbol="AAPL", qty="2.25", avg_entry_price="101.5")]),
                submit_order=Mock(return_value=SimpleNamespace(status="accepted", id="sell-1")),
            )

            with patch.object(bot, "trade_log", trade_log), patch.object(bot, "trading", fake_trading):
                b = bot.Bot(bot.BotConfig(symbol="AAPL", asset_class="stock"))
                b.total_qty = 99.0
                b.entry_price = 80.0
                b.sell_all("stop")
                request = fake_trading.submit_order.call_args.args[0]
                self.assertEqual(request.qty, 2.25)
                self.assertEqual(b.total_qty, 2.25)
                self.assertEqual(b.entry_price, 101.5)
                trade_log.log_order.assert_called_once()


class AddAssetTests(unittest.TestCase):
    def test_validate_symbol_uses_crypto_quote_lookup(self):
        with install_alpaca_stubs():
            add_asset = importlib.import_module("add_asset")
            importlib.reload(add_asset)

            fake_quote_client = SimpleNamespace(
                get_crypto_latest_quote=Mock(return_value={"BTC/USD": object()})
            )
            with patch.object(add_asset, "TradingClient", create=True), patch(
                "alpaca.data.historical.CryptoHistoricalDataClient",
                return_value=fake_quote_client,
            ):
                self.assertIsNone(add_asset.validate_symbol("BTC/USD", "crypto"))

    def test_reload_service_raises_when_launchctl_load_fails(self):
        with install_alpaca_stubs():
            add_asset = importlib.import_module("add_asset")
            importlib.reload(add_asset)

            runs = [
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=1, stdout="", stderr="bad plist"),
            ]
            with patch.object(add_asset.subprocess, "check_output", return_value=b"501"), patch.object(
                add_asset.subprocess, "run", side_effect=runs
            ):
                with self.assertRaisesRegex(RuntimeError, "launchctl load failed"):
                    add_asset.reload_service()


class DashboardAndStatusTests(unittest.TestCase):
    def test_parse_history_tolerates_missing_log(self):
        with install_alpaca_stubs():
            dashboard = importlib.import_module("dashboard")
            importlib.reload(dashboard)
            status = importlib.import_module("status")
            importlib.reload(status)
            with tempfile.TemporaryDirectory() as tmp:
                temp_dir = Path(tmp)
                with patch.object(dashboard, "BOT_LOG_PATH", temp_dir / "bot.log"), patch.object(
                    status, "HERE", temp_dir
                ):
                    self.assertEqual(dashboard.parse_history("AAPL"), ([], [], [], None))
                    self.assertEqual(status.parse_history("AAPL"), ([], [], [], None))

    def test_dashboard_build_html_contains_control_room(self):
        with install_alpaca_stubs():
            dashboard = importlib.import_module("dashboard")
            importlib.reload(dashboard)
            html = dashboard.build_html()
            self.assertIn("Trading Bot Control Room", html)

    def test_env_settings_round_trip(self):
        with install_alpaca_stubs():
            dashboard = importlib.import_module("dashboard")
            importlib.reload(dashboard)
            with tempfile.TemporaryDirectory() as tmp:
                env_path = Path(tmp) / ".env"
                dashboard.save_env_settings("key123", "secret456", "", path=env_path)
                values = dashboard.read_env_settings(env_path)
                self.assertEqual(values["ALPACA_API_KEY"], "key123")
                self.assertEqual(values["ALPACA_SECRET_KEY"], "secret456")
                self.assertEqual(values["ALPACA_BASE_URL"], "https://paper-api.alpaca.markets")

    def test_version_rendering_uses_v_prefix(self):
        with install_alpaca_stubs():
            dashboard = importlib.import_module("dashboard")
            importlib.reload(dashboard)
            with tempfile.TemporaryDirectory() as tmp:
                version_path = Path(tmp) / "VERSION"
                version_path.write_text("46.0\n")
                self.assertEqual(dashboard.read_version(version_path), "46.0")
                self.assertEqual(dashboard.visible_version(version_path), "v46.0")

    def test_service_status_parses_launchctl_list(self):
        with install_alpaca_stubs():
            dashboard = importlib.import_module("dashboard")
            importlib.reload(dashboard)
            with tempfile.TemporaryDirectory() as tmp:
                plist_path = Path(tmp) / "com.trading.bot.plist"
                plist_path.write_bytes(plistlib.dumps({"Label": "com.trading.bot"}))
                output = "-\t0\tcom.apple.foo\n123\t0\tcom.trading.bot\n"
                with patch.object(dashboard, "PLIST_PATH", plist_path), patch.object(
                    dashboard, "launchctl_list", return_value=output
                ):
                    status = dashboard.get_service_status()
                    self.assertTrue(status["available"])
                    self.assertTrue(status["loaded"])
                    self.assertTrue(status["running"])

    def test_load_bots_parses_numeric_values(self):
        with install_alpaca_stubs():
            dashboard = importlib.import_module("dashboard")
            importlib.reload(dashboard)
            with tempfile.TemporaryDirectory() as tmp:
                bot_path = Path(tmp) / "bot.py"
                bot_path.write_text(
                    '_P = 200\n'
                    'BOTS = [\n'
                    '    BotConfig(symbol="AAPL", asset_class="stock", initial_notional=round(_P*0.10, 2), ladder_notional=25, stop_pct=0.9),\n'
                    ']\n'
                )
                bots = dashboard.load_bots(bot_path)
                self.assertEqual(bots[0]["symbol"], "AAPL")
                self.assertEqual(bots[0]["initial_notional"], 20.0)
                self.assertEqual(bots[0]["ladder_notional"], 25.0)
                self.assertEqual(bots[0]["stop_pct"], 0.9)

    def test_write_bots_updates_asset_lines(self):
        with install_alpaca_stubs():
            dashboard = importlib.import_module("dashboard")
            importlib.reload(dashboard)
            with tempfile.TemporaryDirectory() as tmp:
                bot_path = Path(tmp) / "bot.py"
                bot_path.write_text(
                    '_P = 200\n'
                    'BOTS = [\n'
                    '    BotConfig(symbol="AAPL", asset_class="stock", initial_notional=20, ladder_notional=20),\n'
                    ']\n'
                )
                dashboard.write_bots(
                    [{
                        "symbol": "TSLA",
                        "asset_class": "stock",
                        "initial_notional": 35.0,
                        "ladder_notional": 15.0,
                        "stop_pct": 0.9,
                        "trail_trigger": 1.1,
                        "trail_step": 1.05,
                        "trail_stop": 0.95,
                        "ladder1_pct": 0.925,
                        "ladder2_pct": 0.85,
                        "poll_interval": 45,
                    }],
                    bot_path,
                )
                text = bot_path.read_text()
                self.assertIn('symbol="TSLA"', text)
                self.assertIn("poll_interval=45", text)
                self.assertNotIn('symbol="AAPL"', text)

    def test_install_or_repair_launch_agent_writes_plist(self):
        with install_alpaca_stubs():
            dashboard = importlib.import_module("dashboard")
            importlib.reload(dashboard)
            with tempfile.TemporaryDirectory() as tmp:
                plist_path = Path(tmp) / "com.trading.bot.plist"
                with patch.object(dashboard, "PLIST_PATH", plist_path):
                    message = dashboard.install_or_repair_launch_agent()
                    data = plistlib.loads(plist_path.read_bytes())
                    self.assertIn("LaunchAgent installed", message)
                    self.assertEqual(data["Label"], "com.trading.bot")
                    self.assertEqual(data["ProgramArguments"][1], str(dashboard.BOT_PATH))


if __name__ == "__main__":
    unittest.main()
