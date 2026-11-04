"""Tests for the overnight gap-trade pilot (multi-stock)."""
import sys
import unittest
from pathlib import Path
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.overnight_pilot import (
    _calc_position_size, _calc_reward_to_risk, _compute_ticker_score,
    _ema, _ema_slope, _rel_strength, _score_setup, _validate_setup, _vwap,
)


class TestEMA(unittest.TestCase):
    """Test EMA computation."""

    def test_ema_basic(self):
        closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]
        ema = _ema(closes, 5)
        self.assertIsInstance(ema, float)
        self.assertGreater(ema, 14.0)
        self.assertLess(ema, 19.0)

    def test_ema_insufficient_data(self):
        closes = [10.0, 11.0, 12.0]
        ema = _ema(closes, 5)
        self.assertIsNone(ema)


class TestVWAP(unittest.TestCase):
    """Test VWAP computation."""

    def test_vwap_basic(self):
        bars = [
            {"high": 11, "low": 9, "close": 10, "volume": 100},
            {"high": 13, "low": 11, "close": 12, "volume": 200},
            {"high": 15, "low": 13, "close": 14, "volume": 300},
        ]
        vwap = _vwap(bars)
        self.assertIsInstance(vwap, float)
        self.assertAlmostEqual(vwap, 12.67, places=1)


class TestRelativeStrength(unittest.TestCase):
    """Test relative strength vs SPY."""

    def test_rel_strength_outperform(self):
        ticker_closes = [100.0, 105.0, 110.0]
        spy_closes = [100.0, 102.0, 104.0]
        rs = _rel_strength(ticker_closes, spy_closes, lookback=2)
        # Ticker +10%, SPY +4%, relative = +0.06
        self.assertAlmostEqual(rs, 0.06, places=4)

    def test_rel_strength_underperform(self):
        ticker_closes = [100.0, 98.0, 96.0]
        spy_closes = [100.0, 102.0, 104.0]
        rs = _rel_strength(ticker_closes, spy_closes, lookback=2)
        self.assertLess(rs, 0)


class TestPositionSizing(unittest.TestCase):
    """Test position sizing based on 0.25% risk per trade."""

    def test_long_position_size(self):
        equity = Decimal("100000")
        entry = 100.0
        stop = 95.0  # $5 risk distance
        size = _calc_position_size(equity, entry, stop, "long")
        # 0.25% of 100000 = 250. 250 / 5 = 50 shares
        self.assertEqual(size, Decimal("50.00"))

    def test_short_position_size(self):
        equity = Decimal("100000")
        entry = 100.0
        stop = 105.0  # $5 risk distance
        size = _calc_position_size(equity, entry, stop, "short")
        self.assertEqual(size, Decimal("50.00"))

    def test_zero_risk_distance(self):
        size = _calc_position_size(Decimal("100000"), 100.0, 100.0, "long")
        self.assertEqual(size, Decimal("0"))

    def test_invalid_direction_long(self):
        # stop above entry for long = invalid
        size = _calc_position_size(Decimal("100000"), 100.0, 105.0, "long")
        self.assertEqual(size, Decimal("0"))

    def test_invalid_direction_short(self):
        # stop below entry for short = invalid
        size = _calc_position_size(Decimal("100000"), 100.0, 95.0, "short")
        self.assertEqual(size, Decimal("0"))


class TestRewardToRisk(unittest.TestCase):
    """Test reward-to-risk calculation."""

    def test_long_r_r(self):
        r_r = _calc_reward_to_risk(100.0, 95.0, 110.0, "long")
        self.assertEqual(r_r, Decimal("2.00"))

    def test_short_r_r(self):
        r_r = _calc_reward_to_risk(100.0, 105.0, 90.0, "short")
        self.assertEqual(r_r, Decimal("2.00"))

    def test_zero_risk(self):
        r_r = _calc_reward_to_risk(100.0, 100.0, 110.0, "long")
        self.assertEqual(r_r, Decimal("0"))


class TestSetupValidation(unittest.TestCase):
    """Test setup validation logic."""

    def test_long_reclaim_approved(self):
        rec = {
            "ticker": "NVDA",
            "setup": "long_reclaim",
            "price": 100.0,
            "or_low": 95.0,
            "or_high": 105.0,
            "vwap": 98.0,
            "zone_verdict": "long_ok",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.25, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertTrue(approved)
        self.assertEqual(reason, "approved")
        self.assertEqual(plan["direction"], "long")
        self.assertGreater(plan["r_r_ratio"], Decimal("1.5"))

    def test_short_breakdown_approved(self):
        rec = {
            "ticker": "QQQ",
            "setup": "short_breakdown",
            "price": 200.0,
            "or_low": 190.0,
            "or_high": 205.0,
            "vwap": 202.0,
            "zone_verdict": "short_ok",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.25, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertTrue(approved)
        self.assertEqual(plan["direction"], "short")

    def test_zone_blocked_long(self):
        rec = {
            "ticker": "SPY",
            "setup": "long_reclaim",
            "price": 400.0,
            "or_low": 395.0,
            "or_high": 405.0,
            "vwap": 398.0,
            "zone_verdict": "long_blocked",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.25, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("zone blocked", reason)

    def test_zone_blocked_short(self):
        rec = {
            "ticker": "SPY",
            "setup": "short_breakdown",
            "price": 400.0,
            "or_low": 395.0,
            "or_high": 405.0,
            "vwap": 402.0,
            "zone_verdict": "short_blocked",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.25, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("zone blocked", reason)

    def test_odds_error_fail_closed(self):
        rec = {
            "ticker": "NVDA",
            "setup": "long_reclaim",
            "price": 100.0,
            "or_low": 95.0,
            "or_high": 105.0,
            "vwap": 98.0,
            "zone_verdict": "long_ok",
            "odds": {"available": False, "reason": "tool timeout",
                     "error": True},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("time machine unavailable", reason)

    def test_odds_target_too_low(self):
        rec = {
            "ticker": "NVDA",
            "setup": "long_reclaim",
            "price": 100.0,
            "or_low": 95.0,
            "or_high": 105.0,
            "vwap": 98.0,
            "zone_verdict": "long_ok",
            "odds": {"available": True, "target_first": 0.10,
                     "stop_first": 0.30, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("target_first", reason)

    def test_odds_stop_too_high(self):
        rec = {
            "ticker": "NVDA",
            "setup": "long_reclaim",
            "price": 100.0,
            "or_low": 95.0,
            "or_high": 105.0,
            "vwap": 98.0,
            "zone_verdict": "long_ok",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.45, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("stop_first", reason)

    def test_zero_risk_distance_blocks(self):
        rec = {
            "ticker": "NVDA",
            "setup": "long_reclaim",
            "price": 95.0,
            "or_low": 95.0,
            "or_high": 105.0,
            "vwap": 98.0,
            "zone_verdict": "long_ok",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.25, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("no risk distance", reason)


class TestScoreCalculation(unittest.TestCase):
    """Test setup score calculation."""

    def test_score_calculation(self):
        bars = []
        base = 100.0
        for i in range(60):
            bars.append({
                "high": base + i + 2,
                "low": base + i - 1,
                "close": base + i + 1,
                "volume": 1000,
            })
        spy_closes = [100.0 + i * 0.5 for i in range(60)]
        score, factors = _compute_ticker_score("NVDA", bars, spy_closes,
                                               entry_price=159.0,
                                               stop=154.0,
                                               target=169.0)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)
        self.assertIn("trend_alignment", factors)
        self.assertIn("vwap_confluence", factors)
        self.assertIn("rel_strength", factors)
        self.assertIn("liquidity", factors)
        self.assertIn("ema_alignment", factors)
        self.assertIn("historical_perf", factors)


if __name__ == "__main__":
    unittest.main()