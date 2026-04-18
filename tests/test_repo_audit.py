import importlib
import json
import plistlib
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
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

    class GetCalendarRequest:
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
    trading_requests.GetCalendarRequest = GetCalendarRequest
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

    def test_monitor_risk_sells_partial_position_and_sets_cooldown(self):
        with install_alpaca_stubs():
            bot = importlib.import_module("bot")
            importlib.reload(bot)

            trade_log = SimpleNamespace(log_order=Mock())
            fake_trading = SimpleNamespace(
                get_all_positions=Mock(
                    return_value=[SimpleNamespace(symbol="AAPL", qty="10", avg_entry_price="100")]
                ),
                submit_order=Mock(return_value=SimpleNamespace(status="accepted", id="sell-2")),
                get_calendar=Mock(
                    return_value=[
                        SimpleNamespace(date=date(2026, 4, 20)),
                        SimpleNamespace(date=date(2026, 4, 21)),
                        SimpleNamespace(date=date(2026, 4, 22)),
                        SimpleNamespace(date=date(2026, 4, 23)),
                    ]
                ),
            )

            with patch.object(bot, "trade_log", trade_log), patch.object(bot, "trading", fake_trading):
                b = bot.Bot(
                    bot.BotConfig(
                        symbol="AAPL",
                        asset_class="stock",
                        stop_sell_pct=0.55,
                        stop_cooldown_days=3,
                    )
                )
                b.floor = 95.0
                b.trail_next = 105.0
                with patch.object(b, "get_price", return_value=94.0):
                    event = b.monitor_risk(date(2026, 4, 17))
                request = fake_trading.submit_order.call_args.args[0]
                self.assertEqual(request.qty, 5.5)
                self.assertEqual(event["proceeds"], 522.5)
                self.assertEqual(b.stop_ready_on, date(2026, 4, 23))
                trade_log.log_order.assert_called_once()

    def test_add_trading_days_uses_market_calendar(self):
        with install_alpaca_stubs():
            bot = importlib.import_module("bot")
            importlib.reload(bot)

            fake_trading = SimpleNamespace(
                get_calendar=Mock(
                    return_value=[
                        SimpleNamespace(date=date(2026, 1, 20)),
                        SimpleNamespace(date=date(2026, 1, 21)),
                        SimpleNamespace(date=date(2026, 1, 22)),
                    ]
                )
            )
            with patch.object(bot, "trading", fake_trading):
                bot._calendar_cache.clear()
                self.assertEqual(bot.add_trading_days(date(2026, 1, 16), 1), date(2026, 1, 20))

    def test_portfolio_manager_monitors_btc_24x7_by_default(self):
        with install_alpaca_stubs():
            bot = importlib.import_module("bot")
            importlib.reload(bot)

            manager = bot.PortfolioManager(
                [
                    bot.Bot(bot.BotConfig(symbol="AAPL", asset_class="stock")),
                    bot.Bot(bot.BotConfig(symbol="BTC/USD", asset_class="crypto")),
                ]
            )
            self.assertTrue(manager.should_monitor_bot(manager.bot_by_symbol["AAPL"], True))
            self.assertTrue(manager.should_monitor_bot(manager.bot_by_symbol["BTC/USD"], False))

    def test_run_monitors_crypto_when_equity_market_is_closed(self):
        with install_alpaca_stubs():
            bot = importlib.import_module("bot")
            importlib.reload(bot)

            fake_trading = SimpleNamespace(
                get_account=Mock(return_value=SimpleNamespace(cash="0", equity="0")),
                get_all_positions=Mock(return_value=[]),
                get_orders=Mock(return_value=[]),
            )
            with patch.object(bot, "trading", fake_trading):
                crypto_bot = bot.Bot(bot.BotConfig(symbol="BTC/USD", asset_class="crypto"))
                manager = bot.PortfolioManager([crypto_bot])
                fake_clock = SimpleNamespace(
                    is_open=False,
                    timestamp=datetime(2026, 4, 18, 10, 0, tzinfo=timezone.utc),
                    next_close=datetime(2026, 4, 20, 20, 0, tzinfo=timezone.utc),
                    next_open=datetime(2026, 4, 20, 13, 30, tzinfo=timezone.utc),
                )
                with patch.object(manager, "startup_sync"), patch.object(
                    manager, "market_clock", return_value=fake_clock
                ), patch.object(
                    crypto_bot, "monitor_risk", return_value=None
                ) as monitor_risk, patch.object(
                    manager, "should_rebalance", return_value=False
                ), patch.object(
                    manager.logger, "info"
                ), patch.object(
                    manager, "save_state"
                ), patch.object(
                    bot.time, "sleep", side_effect=KeyboardInterrupt
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        manager.run()
                monitor_risk.assert_called_once_with(fake_clock.timestamp.date())

    def test_rebalance_uses_target_weights(self):
        with install_alpaca_stubs():
            bot = importlib.import_module("bot")
            importlib.reload(bot)

            trade_log = SimpleNamespace(log_order=Mock())
            fake_trading = SimpleNamespace(
                get_account=Mock(return_value=SimpleNamespace(equity="1000", cash="0")),
                get_all_positions=Mock(
                    return_value=[
                        SimpleNamespace(symbol="TSLA", qty="5", avg_entry_price="100", market_value="500"),
                        SimpleNamespace(symbol="NVDA", qty="3", avg_entry_price="100", market_value="300"),
                        SimpleNamespace(symbol="BTCUSD", qty="1.25", avg_entry_price="100", market_value="125"),
                    ]
                ),
                get_orders=Mock(return_value=[]),
            )
            with patch.object(bot, "trade_log", trade_log), patch.object(bot, "trading", fake_trading):
                tsla = bot.Bot(bot.BotConfig(symbol="TSLA", asset_class="stock", target_weight=0.50))
                nvda = bot.Bot(bot.BotConfig(symbol="NVDA", asset_class="stock", target_weight=0.25))
                btc = bot.Bot(bot.BotConfig(symbol="BTC/USD", asset_class="crypto", target_weight=0.25))
                manager = bot.PortfolioManager([tsla, nvda, btc])
                with patch.object(manager, "now_et", return_value=datetime(2026, 4, 18, 15, 55, tzinfo=timezone.utc)), patch.object(
                    manager, "settle_sell_orders"
                ), patch.object(bot.time, "sleep"), patch.object(
                    tsla, "get_price", return_value=100.0
                ), patch.object(
                    nvda, "get_price", return_value=100.0
                ), patch.object(
                    btc, "get_price", return_value=100.0
                ), patch.object(
                    tsla, "buy"
                ) as tsla_buy, patch.object(
                    nvda, "buy"
                ) as nvda_buy, patch.object(
                    nvda, "sell_qty"
                ) as nvda_sell:
                    manager.rebalance_portfolio("test")
                tsla_buy.assert_not_called()
                nvda_buy.assert_not_called()
                nvda_sell.assert_called_once()


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
                    'BOTS = [\n'
                    '    BotConfig(symbol="AAPL", asset_class="stock", target_weight=0.35, base_tol=0.009, stop_sell_pct=0.9, stop_cooldown_days=4),\n'
                    ']\n'
                )
                bots = dashboard.load_bots(bot_path)
                self.assertEqual(bots[0]["symbol"], "AAPL")
                self.assertEqual(bots[0]["target_weight"], 0.35)
                self.assertEqual(bots[0]["base_tol"], 0.009)
                self.assertEqual(bots[0]["stop_sell_pct"], 0.9)
                self.assertEqual(bots[0]["stop_cooldown_days"], 4)

    def test_write_bots_updates_asset_lines(self):
        with install_alpaca_stubs():
            dashboard = importlib.import_module("dashboard")
            importlib.reload(dashboard)
            with tempfile.TemporaryDirectory() as tmp:
                bot_path = Path(tmp) / "bot.py"
                bot_path.write_text(
                    'BOTS = [\n'
                    '    BotConfig(symbol="AAPL", asset_class="stock", target_weight=0.2),\n'
                    ']\n'
                )
                dashboard.write_bots(
                    [{
                        "symbol": "TSLA",
                        "asset_class": "stock",
                        "target_weight": 0.50,
                        "base_tol": 0.0123,
                        "trail_step": 1.05,
                        "trail_stop": 0.95,
                        "stop_sell_pct": 0.9,
                        "stop_cooldown_days": 4,
                        "poll_interval": 45,
                    }],
                    bot_path,
                )
                text = bot_path.read_text()
                self.assertIn('symbol="TSLA"', text)
                self.assertIn("target_weight=0.5", text)
                self.assertIn("base_tol=0.0123", text)
                self.assertIn("stop_sell_pct=0.9", text)
                self.assertIn("stop_cooldown_days=4", text)
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


class ArtifactGuardrailTests(unittest.TestCase):
    def test_bot_refit_artifact_does_not_mark_train_winner_as_recommended(self):
        payload = json.loads(Path("bot_refit_results.json").read_text())
        self.assertNotIn("recommended_bot_config", payload)
        self.assertIn("live_default_policy", payload)
        self.assertFalse(payload["live_default_policy"]["auto_promote"])


if __name__ == "__main__":
    unittest.main()
