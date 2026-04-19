import unittest

import copytrade_demo as demo


def make_series(price: float) -> demo.DailySeries:
    return demo.DailySeries(
        days=["2026-01-20", "2026-01-21", "2026-01-22"],
        quotes={
            "2026-01-20": demo.DailyQuote("2026-01-20", price, "2026-01-20T15:00:00Z", price + 0.5, "2026-01-20T20:00:00Z"),
            "2026-01-21": demo.DailyQuote("2026-01-21", price + 1.0, "2026-01-21T15:00:00Z", price + 1.5, "2026-01-21T20:00:00Z"),
            "2026-01-22": demo.DailyQuote("2026-01-22", price + 2.0, "2026-01-22T15:00:00Z", price + 2.5, "2026-01-22T20:00:00Z"),
        },
    )


def make_market():
    return {
        "SPY": make_series(100.0),
        "AAA": make_series(10.0),
        "BBB": make_series(20.0),
    }


class CopyTradeDemoTests(unittest.TestCase):
    def test_large_band_filter(self):
        signal = demo.DisclosureSignal(
            published_at="2026-03-10",
            traded_at="2026-02-25",
            politician="Markwayne Mullin",
            symbol="UNH",
            side="buy",
            size_band="50K-100K",
            source="https://example.com",
        )
        self.assertTrue(demo.qualifies(signal, "50K-100K"))
        self.assertFalse(demo.qualifies(signal, "100K-250K"))

    def test_band_to_points(self):
        signal = demo.DisclosureSignal(
            published_at="2026-01-16",
            traded_at="2025-12-29",
            politician="Markwayne Mullin",
            symbol="MSFT",
            side="buy",
            size_band="100K-250K",
            source="https://example.com",
        )
        self.assertEqual(demo.target_points(signal), 4)

    def test_top_band_gets_20_points(self):
        signal = demo.DisclosureSignal(
            published_at="2026-01-16",
            traded_at="2025-12-29",
            politician="Markwayne Mullin",
            symbol="VIG",
            side="buy",
            size_band="5M-25M",
            source="https://example.com",
        )
        self.assertEqual(demo.target_points(signal), 20)

    def test_lower_bands_get_fewer_points(self):
        mid = demo.DisclosureSignal(
            published_at="2026-01-16",
            traded_at="2025-12-29",
            politician="Markwayne Mullin",
            symbol="MSFT",
            side="buy",
            size_band="15K-50K",
            source="https://example.com",
        )
        low = demo.DisclosureSignal(
            published_at="2026-01-16",
            traded_at="2025-12-29",
            politician="Markwayne Mullin",
            symbol="AAPL",
            side="buy",
            size_band="1K-15K",
            source="https://example.com",
        )
        self.assertEqual(demo.target_points(mid), 2)
        self.assertEqual(demo.target_points(low), 1)

    def test_sub_1k_band_is_ignored(self):
        signal = demo.DisclosureSignal(
            published_at="2026-01-16",
            traded_at="2025-12-29",
            politician="Markwayne Mullin",
            symbol="TINY",
            side="buy",
            size_band="< 1K",
            source="https://example.com",
        )
        self.assertEqual(demo.target_points(signal), 0)

    def test_apply_decay_reduces_existing_points(self):
        raw_points = {"AAA": 4.0, "BBB": 2.0}
        demo._apply_decay(raw_points, days_elapsed=2, daily_decay_pct=0.1)
        self.assertAlmostEqual(raw_points["AAA"], 3.24, places=6)
        self.assertAlmostEqual(raw_points["BBB"], 1.62, places=6)

    def test_queue_inserts_weaker_bands_closer_to_exit(self):
        queue = ["H1", "H2"]
        active_points = {"H1": 4, "H2": 4}
        demo._queue_insert(queue, "M1", 2, {**active_points, "M1": 2})
        demo._queue_insert(queue, "L1", 1, {**active_points, "M1": 2, "L1": 1})
        demo._queue_insert(queue, "M2", 2, {**active_points, "M1": 2, "L1": 1, "M2": 2})
        self.assertEqual(queue, ["L1", "M1", "M2", "H1", "H2"])

    def test_queue_resorts_same_band_by_performance(self):
        market = {
            "AAA": make_series(10.0),
            "BBB": make_series(20.0),
            "CCC": make_series(30.0),
        }
        queue = ["AAA", "BBB", "CCC"]
        sorted_queue = demo._resort_queue(
            queue,
            active_points={"AAA": 4, "BBB": 4, "CCC": 4},
            positions={"AAA": 1.0, "BBB": 1.0, "CCC": 1.0},
            cost_basis={"AAA": 8.0, "BBB": 21.0, "CCC": 35.0},
            market=market,
            day="2026-01-21",
            entry_order={"AAA": 0, "BBB": 1, "CCC": 2},
        )
        self.assertEqual(sorted_queue, ["CCC", "BBB", "AAA"])

    def test_next_trading_day_open_execution(self):
        signal = demo.DisclosureSignal(
            published_at="2026-01-20",
            traded_at="2026-01-15",
            politician="Markwayne Mullin",
            symbol="AAA",
            side="buy",
            size_band="100K-250K",
            source="https://example.com/aaa",
        )
        result = demo.simulate_with_market(
            [signal],
            market=make_market(),
            trading_days=["2026-01-20", "2026-01-21", "2026-01-22"],
            capital=10000.0,
            min_band="50K-100K",
            entry_lag_trading_days=1,
            end="2026-01-22",
            skipped_symbols={},
        )
        buy_event = next(event for event in result["events"] if event["action"] == "buy")
        self.assertEqual(buy_event["trade_day"], "2026-01-21")
        self.assertEqual(buy_event["fill_ts"], "2026-01-21T15:00:00Z")
        self.assertEqual(buy_event["price"], 11.0)

    def test_strategy_normalizes_active_weights(self):
        signals = [
            demo.DisclosureSignal(
                published_at="2026-01-20",
                traded_at="2026-01-15",
                politician="Markwayne Mullin",
                symbol="AAA",
                side="buy",
                size_band="100K-250K",
                source="https://example.com/aaa",
            ),
            demo.DisclosureSignal(
                published_at="2026-01-20",
                traded_at="2026-01-15",
                politician="Markwayne Mullin",
                symbol="BBB",
                side="buy",
                size_band="50K-100K",
                source="https://example.com/bbb",
            ),
        ]
        result = demo.simulate_with_market(
            signals,
            market=make_market(),
            trading_days=["2026-01-20", "2026-01-21", "2026-01-22"],
            capital=10000.0,
            min_band="50K-100K",
            entry_lag_trading_days=1,
            end="2026-01-22",
            skipped_symbols={},
        )
        buy_amount = sum(event["amount"] for event in result["events"] if event["action"] == "buy")
        self.assertLess(result["cash"], 1.0)
        self.assertEqual(result["weight_mode"], "normalized")
        self.assertAlmostEqual(buy_amount, 10000.0, places=2)

    def test_queue_limit_evicts_oldest_strong_name_first(self):
        market = {"SPY": make_series(100.0)}
        signals = []
        for idx in range(11):
            symbol = f"S{idx:02d}"
            market[symbol] = make_series(10.0 + idx)
            signals.append(
                demo.DisclosureSignal(
                    published_at="2026-01-20",
                    traded_at="2026-01-15",
                    politician="Markwayne Mullin",
                    symbol=symbol,
                    side="buy",
                    size_band="100K-250K",
                    source=f"https://example.com/{symbol.lower()}",
                )
            )
        result = demo.simulate_with_market(
            signals,
            market=market,
            trading_days=["2026-01-20", "2026-01-21", "2026-01-22"],
            capital=10000.0,
            min_band="50K-100K",
            max_names=10,
            entry_lag_trading_days=1,
            end="2026-01-22",
            skipped_symbols={},
        )
        evicted = [event["symbol"] for event in result["events"] if event["action"] == "queue_evict"]
        self.assertEqual(evicted, [])
        self.assertEqual(len(result["active_queue"]), 11)
        self.assertEqual(result["effective_queue_limit"], 11)

    def test_band1_burst_expands_then_contracts_limit(self):
        market = {"SPY": make_series(100.0)}
        signals = []
        for idx in range(11):
            symbol = f"S{idx:02d}"
            market[symbol] = make_series(10.0 + idx)
            signals.append(
                demo.DisclosureSignal(
                    published_at="2026-01-20",
                    traded_at="2026-01-15",
                    politician="Markwayne Mullin",
                    symbol=symbol,
                    side="buy",
                    size_band="100K-250K",
                    source=f"https://example.com/{symbol.lower()}",
                )
            )
        signals.append(
            demo.DisclosureSignal(
                published_at="2026-01-21",
                traded_at="2026-01-16",
                politician="Markwayne Mullin",
                symbol="MID",
                side="buy",
                size_band="15K-50K",
                source="https://example.com/mid",
            )
        )
        market["MID"] = make_series(40.0)
        result = demo.simulate_with_market(
            signals,
            market=market,
            trading_days=["2026-01-20", "2026-01-21", "2026-01-22"],
            capital=10000.0,
            min_band="15K-50K",
            max_names=10,
            entry_lag_trading_days=1,
            end="2026-01-22",
            skipped_symbols={},
        )
        evicted = [event["symbol"] for event in result["events"] if event["action"] == "queue_evict"]
        self.assertEqual(evicted, ["MID", "S10"])
        self.assertEqual(len(result["active_queue"]), 10)
        self.assertEqual(result["effective_queue_limit"], 10)
        self.assertNotIn("MID", result["active_queue"])

    def test_top_band_burst_expands_then_contracts_limit(self):
        market = {"SPY": make_series(100.0)}
        signals = []
        for idx in range(11):
            symbol = f"T{idx:02d}"
            market[symbol] = make_series(10.0 + idx)
            signals.append(
                demo.DisclosureSignal(
                    published_at="2026-01-20",
                    traded_at="2026-01-15",
                    politician="Ro Khanna",
                    symbol=symbol,
                    side="buy",
                    size_band="5M-25M",
                    source=f"https://example.com/{symbol.lower()}",
                )
            )
        signals.append(
            demo.DisclosureSignal(
                published_at="2026-01-21",
                traded_at="2026-01-16",
                politician="Ro Khanna",
                symbol="MID",
                side="buy",
                size_band="15K-50K",
                source="https://example.com/mid",
            )
        )
        market["MID"] = make_series(40.0)
        result = demo.simulate_with_market(
            signals,
            market=market,
            trading_days=["2026-01-20", "2026-01-21", "2026-01-22"],
            capital=10000.0,
            min_band="15K-50K",
            max_names=10,
            entry_lag_trading_days=1,
            end="2026-01-22",
            skipped_symbols={},
        )
        evicted = [event["symbol"] for event in result["events"] if event["action"] == "queue_evict"]
        self.assertEqual(evicted, ["MID", "T10"])
        self.assertEqual(len(result["active_queue"]), 10)
        self.assertEqual(result["effective_queue_limit"], 10)
        self.assertNotIn("MID", result["active_queue"])

    def test_repeat_buy_stacks_on_decayed_points(self):
        market = {
            "SPY": demo.DailySeries(
                days=["2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23"],
                quotes={
                    "2026-01-20": demo.DailyQuote("2026-01-20", 100.0, "2026-01-20T15:00:00Z", 100.5, "2026-01-20T20:00:00Z"),
                    "2026-01-21": demo.DailyQuote("2026-01-21", 101.0, "2026-01-21T15:00:00Z", 101.5, "2026-01-21T20:00:00Z"),
                    "2026-01-22": demo.DailyQuote("2026-01-22", 102.0, "2026-01-22T15:00:00Z", 102.5, "2026-01-22T20:00:00Z"),
                    "2026-01-23": demo.DailyQuote("2026-01-23", 103.0, "2026-01-23T15:00:00Z", 103.5, "2026-01-23T20:00:00Z"),
                },
            ),
            "AAA": demo.DailySeries(
                days=["2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23"],
                quotes={
                    "2026-01-20": demo.DailyQuote("2026-01-20", 10.0, "2026-01-20T15:00:00Z", 10.5, "2026-01-20T20:00:00Z"),
                    "2026-01-21": demo.DailyQuote("2026-01-21", 11.0, "2026-01-21T15:00:00Z", 11.5, "2026-01-21T20:00:00Z"),
                    "2026-01-22": demo.DailyQuote("2026-01-22", 12.0, "2026-01-22T15:00:00Z", 12.5, "2026-01-22T20:00:00Z"),
                    "2026-01-23": demo.DailyQuote("2026-01-23", 13.0, "2026-01-23T15:00:00Z", 13.5, "2026-01-23T20:00:00Z"),
                },
            ),
        }
        signals = [
            demo.DisclosureSignal(
                published_at="2026-01-20",
                traded_at="2026-01-15",
                politician="Markwayne Mullin",
                symbol="AAA",
                side="buy",
                size_band="50K-100K",
                source="https://example.com/aaa-1",
            ),
            demo.DisclosureSignal(
                published_at="2026-01-22",
                traded_at="2026-01-16",
                politician="Markwayne Mullin",
                symbol="AAA",
                side="buy",
                size_band="50K-100K",
                source="https://example.com/aaa-2",
            ),
        ]
        result = demo.simulate_with_market(
            signals,
            market=market,
            trading_days=["2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23"],
            capital=10000.0,
            min_band="50K-100K",
            max_names=10,
            daily_decay_pct=0.1,
            entry_lag_trading_days=1,
            end="2026-01-23",
            skipped_symbols={},
        )
        point_updates = [event for event in result["events"] if event["action"] == "point_update" and event["symbol"] == "AAA"]
        self.assertEqual([event["trade_day"] for event in point_updates], ["2026-01-21", "2026-01-23"])
        self.assertAlmostEqual(point_updates[0]["points_after"], 4.0, places=4)
        self.assertAlmostEqual(point_updates[1]["points_after"], 7.24, places=4)

    def test_sell_subtracts_points_instead_of_zeroing_symbol(self):
        market = make_market()
        signals = [
            demo.DisclosureSignal(
                published_at="2026-01-20",
                traded_at="2026-01-15",
                politician="Markwayne Mullin",
                symbol="AAA",
                side="buy",
                size_band="100K-250K",
                source="https://example.com/aaa-buy-1",
            ),
            demo.DisclosureSignal(
                published_at="2026-01-20",
                traded_at="2026-01-15",
                politician="Markwayne Mullin",
                symbol="AAA",
                side="buy",
                size_band="100K-250K",
                source="https://example.com/aaa-buy-2",
            ),
            demo.DisclosureSignal(
                published_at="2026-01-21",
                traded_at="2026-01-16",
                politician="Markwayne Mullin",
                symbol="AAA",
                side="sell",
                size_band="100K-250K",
                source="https://example.com/aaa-sell",
            ),
        ]
        result = demo.simulate_with_market(
            signals,
            market=market,
            trading_days=["2026-01-20", "2026-01-21", "2026-01-22"],
            capital=10000.0,
            min_band="50K-100K",
            max_names=10,
            daily_decay_pct=0.0,
            entry_lag_trading_days=1,
            end="2026-01-22",
            skipped_symbols={},
        )
        point_updates = [event for event in result["events"] if event["action"] == "point_update" and event["symbol"] == "AAA"]
        self.assertAlmostEqual(point_updates[0]["points_after"], 8.0, places=4)
        self.assertAlmostEqual(point_updates[1]["points_after"], 4.0, places=4)


if __name__ == "__main__":
    unittest.main()
