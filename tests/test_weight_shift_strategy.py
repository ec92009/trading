import unittest

from weight_shift_strategy import _shift_down, _shift_up


class WeightShiftStrategyTests(unittest.TestCase):
    def test_shift_down_redistributes_equally(self):
        weights = {"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.2, "E": 0.2}
        before, after = _shift_down(weights, "A", 0.25)
        self.assertAlmostEqual(before, 0.2)
        self.assertAlmostEqual(after, 0.15)
        self.assertAlmostEqual(weights["B"], 0.2125)
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_shift_up_takes_evenly_from_others(self):
        weights = {"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.2, "E": 0.2}
        before, after = _shift_up(weights, "A", 0.10)
        self.assertAlmostEqual(before, 0.2)
        self.assertAlmostEqual(after, 0.22)
        self.assertAlmostEqual(weights["B"], 0.195)
        self.assertAlmostEqual(sum(weights.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
