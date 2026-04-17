import unittest

import copytrade_demo as demo


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

    def test_band_to_weight(self):
        signal = demo.DisclosureSignal(
            published_at="2026-01-16",
            traded_at="2025-12-29",
            politician="Markwayne Mullin",
            symbol="MSFT",
            side="buy",
            size_band="100K-250K",
            source="https://example.com",
        )
        self.assertEqual(demo.target_weight(signal), 0.04)


if __name__ == "__main__":
    unittest.main()
