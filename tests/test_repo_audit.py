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
    data_enums = ModuleType("alpaca.data.enums")
    data_requests = ModuleType("alpaca.data.requests")
    data_timeframe = ModuleType("alpaca.data.timeframe")

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

    class StockBarsRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class CryptoBarsRequest:
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
    data_enums.Adjustment = SimpleNamespace(RAW="raw")
    data_requests.StockLatestQuoteRequest = StockLatestQuoteRequest
    data_requests.CryptoLatestQuoteRequest = CryptoLatestQuoteRequest
    data_requests.StockBarsRequest = StockBarsRequest
    data_requests.CryptoBarsRequest = CryptoBarsRequest
    data_timeframe.TimeFrame = SimpleNamespace(Hour="1Hour")

    modules = {
        "alpaca": alpaca,
        "alpaca.trading": trading,
        "alpaca.trading.client": trading_client,
        "alpaca.trading.requests": trading_requests,
        "alpaca.trading.enums": trading_enums,
        "alpaca.trading.exceptions": trading_exceptions,
        "alpaca.data": data,
        "alpaca.data.historical": data_historical,
        "alpaca.data.enums": data_enums,
        "alpaca.data.requests": data_requests,
        "alpaca.data.timeframe": data_timeframe,
    }
    return patch.dict(sys.modules, modules)


class BotBehaviorTests(unittest.TestCase):
    def test_khanna_daily_live_uses_inclusive_60_day_policy(self):
        with install_alpaca_stubs():
            live = importlib.import_module("khanna_daily.live")
            importlib.reload(live)

            self.assertEqual(live.MIN_BAND, "< 1K")
            self.assertEqual(live.MAX_NAMES, 10)
            self.assertAlmostEqual(live.HALF_LIFE_DAYS, 60.0)
            self.assertAlmostEqual(live.DAILY_DECAY_PCT, 0.01148597964710385)
            self.assertEqual(live.IGNORED_SYMBOLS, {"SPX"})

    def test_khanna_daily_market_data_collapses_hourly_rows(self):
        market_data = importlib.import_module("khanna_daily.market_data")
        importlib.reload(market_data)

        rows = {
            "2026-04-20T14:00:00Z": (10.0, 11.0, 9.5, 11.5),
            "2026-04-20T15:00:00Z": (11.0, 12.0, 10.5, 12.5),
            "2026-04-21T14:00:00Z": (12.5, 12.0, 11.5, 13.0),
        }
        self.assertEqual(
            market_data._daily_rows_from_hourly_rows(rows),
            {
                "2026-04-20": (10.0, 12.0, 9.5, 12.5),
                "2026-04-21": (12.5, 12.0, 11.5, 13.0),
            },
        )

    def test_khanna_daily_market_data_remembers_rejected_symbols(self):
        market_data = importlib.import_module("khanna_daily.market_data")
        importlib.reload(market_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            rejected_path = Path(tmpdir) / "rejected_symbols.json"
            with patch.object(market_data, "REJECTED_SYMBOLS_PATH", rejected_path):
                market_data._remember_rejected_symbol("7410Z", "alpaca rejected symbol")
                market_data._remember_rejected_symbol("SPX", "alpaca rejected symbol")

                self.assertEqual(
                    market_data._load_rejected_symbols(),
                    {"7410Z": "alpaca rejected symbol", "SPX": "alpaca rejected symbol"},
                )

    def test_khanna_daily_market_data_skips_previously_rejected_symbols(self):
        market_data = importlib.import_module("khanna_daily.market_data")
        importlib.reload(market_data)

        fake_series = SimpleNamespace(days=["2026-04-18"], quotes={})
        load_calls: list[str] = []

        def fake_load(symbol: str, *, start: str, end: str):
            load_calls.append(symbol)
            if symbol == "SPY":
                return {"2026-04-18": (1.0, 1.0, 1.0, 1.0)}
            if symbol == "AAPL":
                return {"2026-04-18": (2.0, 2.0, 2.0, 2.0)}
            raise AssertionError(f"unexpected symbol load: {symbol}")

        with patch.object(market_data, "_load_rejected_symbols", return_value={"7410Z": "alpaca rejected symbol"}), patch.object(
            market_data,
            "_load_symbol_daily_rows",
            side_effect=fake_load,
        ), patch.object(market_data.demo, "_build_daily_series", return_value=fake_series):
            trading_days, market, skipped = market_data.load_market_series(
                ["AAPL", "7410Z"],
                start="2026-04-18",
                end="2026-04-18",
            )

        self.assertEqual(trading_days, ["2026-04-18"])
        self.assertEqual(load_calls, ["SPY", "AAPL"])
        self.assertIn("AAPL", market)
        self.assertEqual(skipped, {"7410Z": "alpaca rejected symbol"})

    def test_khanna_signal_updater_parses_trade_detail(self):
        signal_updater = importlib.import_module("khanna_daily.signal_updater")
        importlib.reload(signal_updater)

        html = """
        <html><body>
        <h1>Ro Khanna bought Bank of Montreal (BMO:US) on 2026-03-16</h1>
        <div>buy</div>
        <div>1K–15K</div>
        <div>Ro Khanna</div>
        <div>BMO:US</div>
        <div>Bank of Montreal</div>
        <div>Traded</div><div>2026-03-16</div>
        <div>Published</div><div>2026-04-09</div>
        <div>Filing Summary</div>
        </body></html>
        """
        self.assertEqual(
            signal_updater._parse_trade_detail_html(html, trade_id="20003796681"),
            {
                "politician": "Ro Khanna",
                "published_at": "2026-04-09",
                "side": "buy",
                "size_band": "1K-15K",
                "source": "https://www.capitoltrades.com/trades/20003796681",
                "symbol": "BMO",
                "traded_at": "2026-03-16",
            },
        )

    def test_khanna_signal_updater_merges_new_ro_khanna_trades(self):
        signal_updater = importlib.import_module("khanna_daily.signal_updater")
        importlib.reload(signal_updater)

        existing_rows = [
            {
                "politician": "Ro Khanna",
                "published_at": "2026-04-09",
                "side": "buy",
                "size_band": "1K-15K",
                "source": "https://www.capitoltrades.com/trades/20003796681",
                "symbol": "BMO",
                "traded_at": "2026-03-16",
            }
        ]
        new_row = {
            "politician": "Ro Khanna",
            "published_at": "2026-04-20",
            "side": "buy",
            "size_band": "50K-100K",
            "source": "https://www.capitoltrades.com/trades/20003799999",
            "symbol": "AMZN",
            "traded_at": "2026-04-18",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "copytrade_signals.json"
            path.write_text(json.dumps(existing_rows, indent=2))

            with patch.object(signal_updater, "_fetch_trade_ids", side_effect=[["20003799999", "20003796681"], ["20003796681"]]), patch.object(
                signal_updater,
                "_fetch_trade_record",
                return_value=new_row,
            ):
                result = signal_updater.refresh_politician_signals(path=path, max_pages=4)

            merged_rows = json.loads(path.read_text())
            self.assertEqual(result["added"], 1)
            self.assertEqual(result["pages_scanned"], 2)
            self.assertEqual(merged_rows[-1], new_row)

    def test_khanna_signal_updater_rebuilds_politician_year_caches(self):
        signal_updater = importlib.import_module("khanna_daily.signal_updater")
        importlib.reload(signal_updater)

        rows = [
            {
                "politician": "Ro Khanna",
                "published_at": "2026-04-09",
                "side": "buy",
                "size_band": "1K-15K",
                "source": "https://www.capitoltrades.com/trades/20003796681",
                "symbol": "BMO",
                "traded_at": "2026-03-16",
            },
            {
                "politician": "Josh Gottheimer",
                "published_at": "2025-08-24",
                "side": "sell",
                "size_band": "15K-50K",
                "source": "https://www.capitoltrades.com/trades/20003790000",
                "symbol": "MSFT",
                "traded_at": "2025-08-10",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "copytrade_signals.json"
            cache_dir = Path(tmpdir) / "politicians"
            path.write_text(json.dumps(rows, indent=2))

            with patch.object(signal_updater, "POLITICIANS_CACHE_DIR", cache_dir):
                result = signal_updater.rebuild_politician_year_caches(path=path)

            self.assertEqual(result, {"politicians": 2, "year_files": 2})
            self.assertEqual(
                json.loads((cache_dir / "ro_khanna" / "2026" / "signals.json").read_text()),
                [rows[0]],
            )
            self.assertEqual(
                json.loads((cache_dir / "josh_gottheimer" / "2025" / "signals.json").read_text()),
                [rows[1]],
            )

    def test_khanna_live_refreshes_signals_on_startup(self):
        with install_alpaca_stubs():
            live = importlib.import_module("khanna_daily.live")
            importlib.reload(live)

            manager = live.CopyTradeLiveManager()
            with patch.object(live.signal_updater, "refresh_politician_signals", return_value={"added": 1, "pages_scanned": 1, "total_rows": 2}) as refresh_mock, patch.object(
                manager,
                "load_state",
            ), patch.object(manager.order_sync, "sync_trade_log_until_settled"), patch.object(manager, "evaluate"), patch.object(
                manager,
                "save_state",
            ):
                manager.startup_sync()

            refresh_mock.assert_called_once()

    def test_copytrade_live_uses_inclusive_60_day_policy(self):
        with install_alpaca_stubs():
            copytrade_live = importlib.import_module("copytrade_live")
            importlib.reload(copytrade_live)

            self.assertEqual(copytrade_live.MIN_BAND, "< 1K")
            self.assertEqual(copytrade_live.MAX_NAMES, 10)
            self.assertAlmostEqual(copytrade_live.HALF_LIFE_DAYS, 60.0)
            self.assertAlmostEqual(copytrade_live.DAILY_DECAY_PCT, 0.01148597964710385)
            self.assertEqual(
                copytrade_live.LIVE_POINT_SYSTEM,
                {
                    "< 1K": 0.125,
                    "1K-15K": 0.25,
                    "15K-50K": 0.5,
                    "50K-100K": 1.0,
                    "100K-250K": 1.0,
                    "250K-500K": 2.0,
                    "500K-1M": 4.0,
                    "1M-5M": 10.0,
                    "5M-25M": 20.0,
                },
            )

    def test_copytrade_live_point_system_restores_demo_defaults(self):
        with install_alpaca_stubs():
            copytrade_demo = importlib.import_module("copytrade_demo")
            copytrade_live = importlib.import_module("copytrade_live")
            importlib.reload(copytrade_demo)
            importlib.reload(copytrade_live)

            original_points = dict(copytrade_demo.BAND_POINTS)
            with copytrade_live._live_point_system():
                self.assertEqual(copytrade_demo.BAND_POINTS["< 1K"], 0.125)
                self.assertEqual(copytrade_demo.BAND_POINTS["1M-5M"], 10.0)
            self.assertEqual(copytrade_demo.BAND_POINTS, original_points)

    def test_copytrade_signature_tracks_trade_day_and_weights(self):
        with install_alpaca_stubs():
            copytrade_live = importlib.import_module("copytrade_live")
            importlib.reload(copytrade_live)

            result = {"trade_window": {"last_trade_day": "2026-04-10"}}
            weights = {"MSFT": 0.4, "NVDA": 0.6}
            self.assertEqual(
                copytrade_live._signature_for(result, weights),
                "2026-04-10|MSFT:0.4000,NVDA:0.6000",
            )

    def test_copytrade_weights_only_include_positive_targets(self):
        with install_alpaca_stubs():
            copytrade_live = importlib.import_module("copytrade_live")
            importlib.reload(copytrade_live)

            simulation = {
                "positions": {
                    "MSFT": {"weight": 0.55},
                    "NVDA": {"weight": 0},
                    "TSLA": {"weight": 0.4499},
                }
            }
            self.assertEqual(
                copytrade_live._weights_from_simulation(simulation),
                {"MSFT": 0.55, "TSLA": 0.4499},
            )

    def test_copytrade_cancels_open_orders_before_rebalance(self):
        with install_alpaca_stubs():
            copytrade_live = importlib.import_module("copytrade_live")
            importlib.reload(copytrade_live)

            fake_trading = SimpleNamespace(
                get_orders=Mock(
                    return_value=[
                        SimpleNamespace(id="ord-1", symbol="TSLA"),
                        SimpleNamespace(id="ord-2", symbol="NVDA"),
                    ]
                ),
                cancel_order_by_id=Mock(),
            )
            manager = copytrade_live.CopyTradeLiveManager()
            with patch.object(copytrade_live.basket_bot, "trading", fake_trading):
                canceled = manager.cancel_open_orders()

            self.assertEqual(canceled, 2)
            self.assertEqual(fake_trading.cancel_order_by_id.call_count, 2)
            fake_trading.cancel_order_by_id.assert_any_call("ord-1")
            fake_trading.cancel_order_by_id.assert_any_call("ord-2")

    def test_copytrade_run_survives_startup_error(self):
        with install_alpaca_stubs():
            copytrade_live = importlib.import_module("copytrade_live")
            importlib.reload(copytrade_live)

            manager = copytrade_live.CopyTradeLiveManager()
            with patch.object(manager, "startup_sync", side_effect=RuntimeError("boom")), patch.object(
                manager, "market_clock", side_effect=KeyboardInterrupt
            ), patch.object(copytrade_live.time, "sleep") as sleep_mock:
                with self.assertRaises(KeyboardInterrupt):
                    manager.run()

            sleep_mock.assert_called_once_with(30)

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

            with patch.object(bot, "trade_log", trade_log), patch.object(bot, "trading", fake_trading), patch.object(
                bot, "LIVE_REBALANCE_ONLY", False
            ):
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

            trade_log = SimpleNamespace(log_order=Mock(), all_rows=Mock(return_value=[]))
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
                    manager, "sync_trade_log_until_settled"
                ), patch.object(
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

    def test_sync_trade_log_until_settled_rechecks_pending_orders(self):
        with install_alpaca_stubs():
            bot = importlib.import_module("bot")
            importlib.reload(bot)

            manager = bot.PortfolioManager([])
            with patch.object(manager, "sync_trade_log", side_effect=[2, 1, 0]) as sync_mock, patch.object(
                bot.time,
                "sleep",
            ) as sleep_mock:
                pending = manager.sync_trade_log_until_settled(timeout_seconds=30, poll_interval_seconds=5)

            self.assertEqual(pending, 0)
            self.assertEqual(sync_mock.call_count, 3)
            self.assertEqual(sleep_mock.call_count, 2)

    def test_khanna_live_waits_for_pending_order_settlement_after_rebalance(self):
        with install_alpaca_stubs():
            live = importlib.import_module("khanna_daily.live")
            importlib.reload(live)

            manager = live.CopyTradeLiveManager()
            result = {
                "trade_window": {"first_trade_day": "2026-04-20", "last_trade_day": "2026-04-21"},
                "effective_queue_limit": 10,
                "active_queue": ["AMZN"],
                "positions": {"AMZN": {"weight": 1.0}},
            }
            with patch.object(manager, "market_open", return_value=True), patch.object(
                manager,
                "cancel_open_orders",
                return_value=0,
            ), patch.object(manager, "now_et", return_value=datetime(2026, 4, 21, 14, 0, tzinfo=timezone.utc)), patch.object(
                manager,
                "simulate_target_book",
                return_value=result,
            ), patch.object(manager, "rebalance_to_weights") as rebalance_mock, patch.object(
                manager.order_sync,
                "sync_trade_log_until_settled",
            ) as sync_until_mock, patch.object(manager, "save_state"):
                manager.evaluate(reason="Khanna copy-trade rebalance")

            rebalance_mock.assert_called_once()
            sync_until_mock.assert_called_once()


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
