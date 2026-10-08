"""Pullback/push trigger tests (synthetic bars, no network)."""
from __future__ import annotations

import unittest
from decimal import Decimal

from robinhood_tools.errors import PolicyViolation
from robinhood_tools.pullback_push import evaluate, levels_from_bars


def bars(base: float = 100.0, n: int = 40, drift: float = 0.3):
    closes = [base + i * drift for i in range(n)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return closes, highs, lows


class TriggerTest(unittest.TestCase):
    def test_wait_mid_range(self):
        closes, highs, lows = bars()
        levels = levels_from_bars(closes, highs, lows)
        # well above support+zone, below resistance -> WAIT
        mid = levels.pullback_high + (levels.resistance - levels.pullback_high) / 2
        self.assertEqual(evaluate(mid, levels).signal, "WAIT")

    def test_pullback_zone_watch(self):
        closes, highs, lows = bars()
        levels = levels_from_bars(closes, highs, lows)
        probe = (levels.pullback_low + levels.pullback_high) / 2
        state = evaluate(probe, levels)
        self.assertEqual(state.signal, "CALL_WATCH")

    def test_breakdown_put(self):
        closes, highs, lows = bars()
        levels = levels_from_bars(closes, highs, lows)
        state = evaluate(levels.invalidation - Decimal("1"), levels)
        self.assertEqual(state.signal, "PUT_TRIGGER")

    def test_breakout_call(self):
        closes, highs, lows = bars()
        levels = levels_from_bars(closes, highs, lows)
        state = evaluate(levels.resistance + Decimal("1"), levels)
        self.assertEqual(state.signal, "CALL_TRIGGER")

    def test_insufficient_bars(self):
        with self.assertRaises(PolicyViolation):
            levels_from_bars([100.0] * 10, [101.0] * 10, [99.0] * 10)


if __name__ == "__main__":
    unittest.main()
