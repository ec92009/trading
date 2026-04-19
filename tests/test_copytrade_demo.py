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

    def test_queue_inserts_weaker_bands_closer_to_exit(self):
        queue = ["H1", "H2"]
        active_points = {"H1": 4, "H2": 4}
        demo._queue_insert(queue, "M1", 2, {**active_points, "M1": 2})
        demo._queue_insert(queue, "L1", 1, {**active_points, "M1": 2, "L1": 1})
        demo._queue_insert(queue, "M2", 2, {**active_points, "M1": 2, "L1": 1, "M2": 2})
        self.assertEqual(queue, ["L1", "M1", "M2", "H1", "H2"])

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
        self.assertEqual(evicted, ["S00"])
        self.assertEqual(len(result["active_queue"]), 10)
        self.assertNotIn("S00", result["active_queue"])


if __name__ == "__main__":
    unittest.main()
