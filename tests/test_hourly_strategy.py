import unittest

from hourly_strategy import HourlyConfig, simulate_hourly


def make_data(*, aaa_next_open: float = 70.0):
    timestamps = [
        "2023-01-03T00:00:00Z",
        "2023-01-03T01:00:00Z",
        "2023-01-03T15:00:00Z",
        "2023-01-03T16:00:00Z",
        "2023-01-03T20:00:00Z",
        "2023-01-04T00:00:00Z",
        "2023-01-04T15:00:00Z",
        "2023-01-04T16:00:00Z",
        "2023-01-04T20:00:00Z",
    ]
    trading_days = ["2023-01-03", "2023-01-04"]
    assets = {
        "AAA": {
            "opens": [100.0, 100.0, 100.0, 100.0, aaa_next_open, 80.0, 82.0, 84.0, 84.0],
            "closes": [100.0, 100.0, 100.0, 80.0, 80.0, 80.0, 82.0, 84.0, 84.0],
            "lows": [100.0, 100.0, 100.0, 75.0, 80.0, 80.0, 80.0, 83.0, 84.0],
            "highs": [100.0, 100.0, 100.0, 81.0, 80.0, 80.0, 83.0, 85.0, 84.0],
        },
        "BBB": {
            "opens": [100.0, 100.0, 100.0, 100.0, 200.0, 200.0, 180.0, 175.0, 175.0],
            "closes": [100.0, 100.0, 100.0, 200.0, 200.0, 200.0, 180.0, 175.0, 175.0],
            "lows": [100.0, 100.0, 100.0, 199.0, 200.0, 200.0, 179.0, 174.0, 175.0],
            "highs": [100.0, 100.0, 100.0, 201.0, 200.0, 200.0, 181.0, 176.0, 175.0],
        },
        "BTC/USD": {
            "opens": [100.0, 100.0, 100.0, 100.0, 100.0, 95.0, 100.0, 100.0, 100.0],
            "closes": [100.0, 90.0, 100.0, 100.0, 100.0, 95.0, 100.0, 100.0, 100.0],
            "lows": [100.0, 85.0, 100.0, 100.0, 100.0, 90.0, 100.0, 100.0, 100.0],
            "highs": [100.0, 91.0, 100.0, 100.0, 100.0, 96.0, 100.0, 100.0, 100.0],
        },
        "SPY": {
            "opens": [100.0] * len(timestamps),
            "closes": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            "lows": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            "highs": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        },
    }
    betas = {
        "AAA": [1.0] * len(timestamps),
        "BBB": [1.0] * len(timestamps),
        "BTC/USD": [1.0] * len(timestamps),
    }
    return {
        "timestamps": timestamps,
        "dates": timestamps,
        "trading_days": trading_days,
        "stock_timestamps": [
            "2023-01-03T15:00:00Z",
            "2023-01-03T16:00:00Z",
            "2023-01-03T20:00:00Z",
            "2023-01-04T15:00:00Z",
            "2023-01-04T16:00:00Z",
            "2023-01-04T20:00:00Z",
        ],
        "rebalance_timestamps": ["2023-01-03T20:00:00Z", "2023-01-04T20:00:00Z"],
        "assets": assets,
        "betas": betas,
    }


def make_settlement_data():
    timestamps = [
        "2023-01-03T15:00:00Z",
        "2023-01-03T20:00:00Z",
        "2023-01-04T15:00:00Z",
        "2023-01-04T20:00:00Z",
    ]
    return {
        "timestamps": timestamps,
        "dates": timestamps,
        "trading_days": ["2023-01-03", "2023-01-04"],
        "stock_timestamps": timestamps,
        "rebalance_timestamps": ["2023-01-03T20:00:00Z", "2023-01-04T20:00:00Z"],
        "assets": {
            "AAA": {
                "opens": [100.0, 200.0, 200.0, 200.0],
                "closes": [100.0, 200.0, 200.0, 200.0],
                "lows": [100.0, 200.0, 200.0, 200.0],
                "highs": [100.0, 200.0, 200.0, 200.0],
            },
            "BTC/USD": {
                "opens": [100.0, 50.0, 50.0, 50.0],
                "closes": [100.0, 50.0, 50.0, 50.0],
                "lows": [100.0, 50.0, 50.0, 50.0],
                "highs": [100.0, 50.0, 50.0, 50.0],
            },
            "SPY": {
                "opens": [100.0] * len(timestamps),
                "closes": [100.0] * len(timestamps),
                "lows": [100.0] * len(timestamps),
                "highs": [100.0] * len(timestamps),
            },
        },
        "betas": {"AAA": [1.0] * len(timestamps), "BTC/USD": [1.0] * len(timestamps)},
    }


def make_small_drift_data():
    timestamps = ["2023-01-03T15:00:00Z", "2023-01-03T20:00:00Z"]
    return {
        "timestamps": timestamps,
        "dates": timestamps,
        "trading_days": ["2023-01-03"],
        "stock_timestamps": timestamps,
        "rebalance_timestamps": ["2023-01-03T20:00:00Z"],
        "assets": {
            "AAA": {
                "opens": [100.0, 102.0],
                "closes": [100.0, 102.0],
                "lows": [100.0, 102.0],
                "highs": [100.0, 102.0],
            },
            "BBB": {
                "opens": [100.0, 98.0],
                "closes": [100.0, 98.0],
                "lows": [100.0, 98.0],
                "highs": [100.0, 98.0],
            },
            "SPY": {
                "opens": [100.0, 100.0],
                "closes": [100.0, 100.0],
                "lows": [100.0, 100.0],
                "highs": [100.0, 100.0],
            },
        },
        "betas": {"AAA": [1.0, 1.0], "BBB": [1.0, 1.0]},
    }


class HourlyStrategyTests(unittest.TestCase):
    def test_stock_sale_proceeds_stay_unsettled_until_next_trading_day(self):
        data = make_settlement_data()
        cfg = HourlyConfig(
            initial=1000.0,
            target_weights={"AAA": 0.5, "BTC/USD": 0.5},
            enable_risk_controls=False,
        )
        result = simulate_hourly(cfg, data, chosen_symbols=["AAA", "BTC/USD"])
        buy_events = [
            e for e in result["events"] if e["symbol"] == "BTC" and e["action"] == "REBALANCE — bought"
        ]
        self.assertEqual(len(buy_events), 1)
        self.assertEqual(buy_events[0]["date"], "2023-01-04T20:00:00Z")
        day1_assets = next(item["assets"] for item in result["history"] if item["date"] == "2023-01-03T20:00:00Z")
        self.assertEqual(day1_assets["Cash"], 0.0)
        self.assertGreater(day1_assets["Unsettled Cash"], 0.0)

    def test_small_rebalance_drifts_are_skipped_under_min_threshold(self):
        data = make_small_drift_data()
        cfg = HourlyConfig(
            initial=1000.0,
            target_weights={"AAA": 0.5, "BBB": 0.5},
            min_rebalance_notional=25.0,
            min_order_notional=25.0,
            enable_risk_controls=False,
        )
        result = simulate_hourly(cfg, data, chosen_symbols=["AAA", "BBB"])
        rebalance_events = [e for e in result["events"] if "REBALANCE" in e["action"]]
        self.assertEqual(rebalance_events, [])

    def test_fractional_stocks_are_default_live_parity_mode(self):
        data = make_data()
        cfg = HourlyConfig(
            initial=1001.0,
            target_weights={"AAA": 0.5, "BBB": 0.5},
            enable_risk_controls=False,
        )
        result = simulate_hourly(cfg, data, chosen_symbols=["AAA", "BBB"])
        first_assets = result["history"][0]["assets"]
        self.assertEqual(first_assets["AAA"], 500.5)
        self.assertEqual(first_assets["BBB"], 500.5)
        self.assertEqual(first_assets["Cash"], 0.0)

    def test_whole_share_mode_still_exists_for_comparison_runs(self):
        data = make_data()
        cfg = HourlyConfig(
            initial=1001.0,
            target_weights={"AAA": 0.5, "BBB": 0.5},
            fractional_stocks=False,
            enable_risk_controls=False,
        )
        result = simulate_hourly(cfg, data, chosen_symbols=["AAA", "BBB"])
        first_assets = result["history"][0]["assets"]
        self.assertEqual(first_assets["AAA"], 500.0)
        self.assertEqual(first_assets["BBB"], 500.0)
        self.assertEqual(first_assets["Cash"], 1.0)

    def test_stop_executes_on_next_tradable_bar(self):
        data = make_data(aaa_next_open=70.0)
        cfg = HourlyConfig(
            initial=1200.0,
            target_weights={"AAA": 0.5, "BBB": 0.5},
            base_tol=0.05,
            stop_sell_pct=0.50,
            trail_step=1.02,
            trail_stop=0.99,
            stop_cooldown_days=0,
            rebalance_every_bars=1,
            enable_risk_controls=True,
        )
        result = simulate_hourly(cfg, data, chosen_symbols=["AAA", "BBB"])
        stop_events = [e for e in result["events"] if e["symbol"] == "AAA" and "STOP" in e["action"]]
        self.assertGreaterEqual(len(stop_events), 2)
        armed, sold = stop_events[:2]
        self.assertEqual(armed["action"], "STOP — armed")
        self.assertEqual(armed["date"], "2023-01-03T16:00:00Z")
        self.assertEqual(sold["action"], "STOP — sold")
        self.assertEqual(sold["date"], "2023-01-03T20:00:00Z")
        self.assertEqual(sold["price"], 70.0)

    def test_stop_fill_never_improves_past_floor(self):
        data = make_data(aaa_next_open=99.0)
        cfg = HourlyConfig(
            initial=1200.0,
            target_weights={"AAA": 0.5, "BBB": 0.5},
            base_tol=0.05,
            stop_sell_pct=0.50,
            trail_step=1.02,
            trail_stop=0.99,
            stop_cooldown_days=0,
            rebalance_every_bars=1,
            enable_risk_controls=True,
        )
        result = simulate_hourly(cfg, data, chosen_symbols=["AAA", "BBB"])
        sold = next(e for e in result["events"] if e["symbol"] == "AAA" and e["action"] == "STOP — sold")
        self.assertEqual(sold["price"], 95.0)

    def test_disable_risk_controls_removes_stops(self):
        data = make_data()
        cfg = HourlyConfig(
            initial=1200.0,
            base_tol=0.05,
            stop_sell_pct=0.50,
            trail_step=1.02,
            trail_stop=0.99,
            rebalance_every_bars=1,
            enable_risk_controls=False,
        )
        result = simulate_hourly(cfg, data, chosen_symbols=["AAA", "BBB", "BTC/USD"])
        self.assertEqual(result["summary"]["n_stops"], 0)
        self.assertGreaterEqual(result["summary"]["n_rebalances"], 1)

    def test_enable_risk_controls_can_trigger_stop(self):
        data = make_data()
        cfg = HourlyConfig(
            initial=1200.0,
            base_tol=0.05,
            stop_sell_pct=0.50,
            trail_step=1.02,
            trail_stop=0.99,
            stop_cooldown_days=0,
            rebalance_every_bars=1,
            enable_risk_controls=True,
        )
        result = simulate_hourly(cfg, data, chosen_symbols=["AAA", "BBB", "BTC/USD"])
        self.assertGreaterEqual(result["summary"]["n_stops"], 1)

    def test_rebalance_only_happens_on_last_stock_bar(self):
        data = make_data()
        cfg = HourlyConfig(
            initial=1200.0,
            base_tol=0.05,
            stop_sell_pct=0.50,
            trail_step=1.02,
            trail_stop=0.99,
            rebalance_every_bars=1,
            enable_risk_controls=False,
        )
        result = simulate_hourly(cfg, data, chosen_symbols=["AAA", "BBB", "BTC/USD"])
        rebalance_times = [e["date"] for e in result["events"] if "REBALANCE" in e["action"]]
        self.assertTrue(rebalance_times)
        self.assertTrue(all(ts.endswith("20:00:00Z") for ts in rebalance_times))

    def test_friction_reduces_final_value(self):
        data = make_data()
        base_cfg = HourlyConfig(
            initial=1200.0,
            base_tol=0.05,
            stop_sell_pct=0.50,
            trail_step=1.02,
            trail_stop=0.99,
            stop_cooldown_days=0,
            rebalance_every_bars=1,
            enable_risk_controls=True,
        )
        costly_cfg = HourlyConfig(
            initial=1200.0,
            base_tol=0.05,
            stop_sell_pct=0.50,
            trail_step=1.02,
            trail_stop=0.99,
            stop_cooldown_days=0,
            rebalance_every_bars=1,
            enable_risk_controls=True,
            stock_slippage_bps=5.0,
            crypto_slippage_bps=10.0,
            crypto_taker_fee_bps=25.0,
            equity_sec_sell_fee_rate=0.00002060,
            equity_taf_per_share=0.000195,
            equity_taf_max_per_trade=9.79,
            equity_cat_per_share=0.000046,
        )
        base = simulate_hourly(base_cfg, data, chosen_symbols=["AAA", "BBB", "BTC/USD"])
        costly = simulate_hourly(costly_cfg, data, chosen_symbols=["AAA", "BBB", "BTC/USD"])
        self.assertLess(costly["summary"]["final"], base["summary"]["final"])

    def test_custom_target_weights_change_initial_allocation(self):
        data = make_data()
        cfg = HourlyConfig(
            initial=1200.0,
            target_weights={"AAA": 0.5, "BBB": 0.25, "BTC/USD": 0.25},
            enable_risk_controls=False,
        )
        result = simulate_hourly(cfg, data, chosen_symbols=["AAA", "BBB", "BTC/USD"])
        first_assets = result["history"][0]["assets"]
        self.assertGreater(first_assets["AAA"], first_assets["BBB"])
        self.assertEqual(first_assets["BBB"], first_assets["BTC"] if "BTC" in first_assets else first_assets["BTC/USD"])

    def test_non_btc_crypto_symbol_keeps_24x7_risk_checks(self):
        data = make_data()
        data["assets"]["ETH/USD"] = data["assets"].pop("BTC/USD")
        data["betas"]["ETH/USD"] = data["betas"].pop("BTC/USD")
        cfg = HourlyConfig(
            initial=1200.0,
            base_tol=0.05,
            stop_sell_pct=0.50,
            trail_step=1.02,
            trail_stop=0.99,
            stop_cooldown_days=0,
            rebalance_every_bars=1,
            enable_risk_controls=True,
        )
        result = simulate_hourly(cfg, data, chosen_symbols=["AAA", "BBB", "ETH/USD"])
        eth_stop_times = [
            e["date"] for e in result["events"] if e["symbol"] == "ETH/USD" and "STOP" in e["action"]
        ]
        self.assertTrue(eth_stop_times)
        self.assertTrue(any(ts not in data["stock_timestamps"] for ts in eth_stop_times))


if __name__ == "__main__":
    unittest.main()
