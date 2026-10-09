"""Signal engine tests (synthetic bars, exact expectations)."""
from __future__ import annotations

import unittest

from robinhood_tools.signal_engine import (atr, confidence_and_rating,
                                           confirmation_score, ema,
                                           leadership_score, rsi)


def trend_bars(n: int = 120, drift: float = 1.0):
    closes = [300.0 + i * drift for i in range(n)]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    volumes = [10_000_000.0] * n
    return closes, highs, lows, volumes


class SignalEngineTest(unittest.TestCase):
    def test_ema_seed_and_direction(self):
        out = ema([1.0, 2.0, 3.0, 4.0, 5.0], 3)
        self.assertIsNone(out[0])
        self.assertIsNone(out[1])
        self.assertAlmostEqual(out[2], 2.0)
        self.assertGreater(out[4], out[3])

    def test_rsi_bounds(self):
        up = [100.0 + i for i in range(30)]
        down = [200.0 - i for i in range(30)]
        self.assertGreater(rsi(up), 70)
        self.assertLess(rsi(down), 30)
        self.assertIsNone(rsi([1.0] * 5))

    def test_atr_positive(self):
        closes, highs, lows, _ = trend_bars()
        value = atr(highs, lows, closes)
        self.assertGreater(value, 0)

    def test_confirmation_bullish_uptrend(self):
        closes, highs, lows, volumes = trend_bars()
        self.assertGreaterEqual(confirmation_score(closes, highs, lows, volumes), 60.0)

    def test_confirmation_bearish_downtrend(self):
        closes = [500.0 - i * 1.0 for i in range(120)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        volumes = [10_000_000.0] * 120
        self.assertLessEqual(confirmation_score(closes, highs, lows, volumes), -60.0)

    def test_confirmation_needs_bars(self):
        with self.assertRaises(ValueError):
            confirmation_score([1.0] * 10, [1.0] * 10, [1.0] * 10, [1.0] * 10)

    def test_leadership_weighted(self):
        scores = {"QQQ": 100.0, "DIA": 100.0, "IWM": 100.0, "VIX": 100.0,
                  "XLK": 100.0, "XLF": 100.0, "XLY": 100.0}  # VIX pre-inverted
        self.assertAlmostEqual(leadership_score(scores), 100.0)
        flat = {k: 0.0 for k in scores}
        self.assertEqual(leadership_score(flat), 0.0)

    def test_rating_thresholds(self):
        self.assertEqual(confidence_and_rating(100, 100, 100, 100)[1], "STRONG_CALL")
        self.assertEqual(confidence_and_rating(60, 60, 60, 60)[1], "CALL")
        self.assertEqual(confidence_and_rating(0, 0, 0, 0)[1], "NO_TRADE")
        self.assertEqual(confidence_and_rating(-60, -60, -60, -60)[1], "PUT")
        self.assertEqual(confidence_and_rating(-100, -100, -100, -100)[1], "STRONG_PUT")
        conf, _ = confidence_and_rating(60, 60, 60, 60)
        self.assertAlmostEqual(conf, 80.0)


if __name__ == "__main__":
    unittest.main()
