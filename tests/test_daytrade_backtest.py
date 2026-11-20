"""Tests for the day-trade pipeline backtest.

These cover the seams the backtest owns: the shared classification rule, the
point-in-time regime read, the exit/cost simulation that consumes a real
plan, and the long/short aggregation. The gates themselves
(`_validate_setup`, `zone_analysis.verdict_from_bars`, the risk controller)
are deliberately NOT retested here - they have their own suites, and a second
copy of their expectations in this file is how a backtest drifts from the
desk it claims to measure.
"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.daytrade_backtest import (
    BACKTEST_ODDS,
    _aggregate,
    _regime_for,
    _simulate_exit,
)
from scripts.daytrade_scout import classify_direction
from robinhood_tools.market_prices import market_regime


def _bars(specs):
    return [{"datetime": f"2026-11-05 10:{i:02d}:00-05:00",
             "open": o, "high": h, "low": l, "close": c, "volume": v}
            for i, (o, h, l, c, v) in enumerate(specs)]


class ClassificationTests(unittest.TestCase):
    """The rule the live scout and the backtest must share."""

    def test_long_reclaim(self):
        self.assertEqual(classify_direction(101.0, 100.0, 99.0, 102.0), "long")

    def test_short_breakdown(self):
        self.assertEqual(classify_direction(99.0, 100.0, 98.0, 102.0), "short")

    def test_inside_the_range_is_range(self):
        self.assertEqual(classify_direction(100.0, 100.0, 99.0, 102.0), "range")

    def test_no_vwap_is_range(self):
        self.assertEqual(classify_direction(101.0, None, 99.0, 102.0), "range")


class RegimeTests(unittest.TestCase):
    """The regime the backtest reports, using the desk's own function."""

    def _spy(self, n=60, last=200.0, flat=100.0):
        return [(f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", flat)
                for i in range(n - 1)] + [("2026-03-01", last)]

    def test_risk_on(self):
        self.assertEqual(_regime_for(self._spy(last=200.0), "2026-03-01"),
                         "RISK_ON")

    def test_risk_off_below_the_mean(self):
        # market_regime is the desk's rule: below the 50-day mean is RISK_OFF
        self.assertEqual(_regime_for(self._spy(last=50.0), "2026-03-01"),
                         "RISK_OFF")

    def test_short_history_is_unknown(self):
        self.assertEqual(_regime_for([("2026-01-01", 100.0)], "2026-01-01"),
                         "UNKNOWN")

    def test_a_session_is_never_judged_by_its_own_move(self):
        """The prefix stops at `before_date`, so the session's own close is
        excluded - otherwise a session's regime would use its own outcome."""
        rising = [(f"2026-01-{d:02d}", 100.0) for d in range(1, 60)]
        rising.append(("2026-01-60", 101.0))          # 60 closes, still above
        rising.append(("2026-02-01", 50.0))           # the session being judged
        rising.append(("2026-02-02", 50.0))
        self.assertEqual(_regime_for(rising, "2026-01-60"), "RISK_ON")
        # Including the session's own slide would flip it; it must not be included
        self.assertEqual(_regime_for(rising, "2026-02-01"), "RISK_OFF")

    def test_no_spy_history_is_unknown(self):
        self.assertEqual(_regime_for([], "2026-02-01"), "UNKNOWN")


class ExitSimulationTests(unittest.TestCase):
    """Costs and fills, using levels the real `_validate_setup` produced."""

    PLAN = {"direction": "long", "entry": 100.0, "stop": 98.0,
            "target": 104.0, "qty": 10, "setup": "long_reclaim"}

    def test_target_hit_closes_at_the_target_with_costs(self):
        bars = _bars([(100, 101, 99.5, 100.5, 1000)] * 7 +
                     [(101, 105, 100.5, 104.5, 1000)] * 3)
        r = _simulate_exit(bars, 6, self.PLAN, 0.005, 5.0)
        self.assertEqual(r["exit_reason"], "target")
        self.assertAlmostEqual(r["exit"], 104.0 * (1 - 0.0005), places=2)
        self.assertAlmostEqual(r["commission"], 0.005 * 10 * 2, places=4)
        self.assertGreater(r["net_pnl"], 0)
        self.assertEqual(r["borrow_fee"], 0.0)

    def test_stop_hit_on_a_long(self):
        bars = _bars([(100, 100.5, 99.5, 100.0, 1000)] * 7 +
                     [(99, 99.5, 96.0, 97.0, 1000)] * 3)
        r = _simulate_exit(bars, 6, self.PLAN, 0.005, 5.0)
        self.assertEqual(r["exit_reason"], "stop")
        self.assertAlmostEqual(r["exit"], 98.0 * (1 - 0.0005), places=2)
        self.assertLess(r["net_pnl"], 0)

    def test_end_of_day_exit_when_neither_level_touches(self):
        bars = _bars([(100, 100.5, 99.5, 100.2, 1000)] * 12)
        r = _simulate_exit(bars, 6, self.PLAN, 0.005, 5.0)
        self.assertEqual(r["exit_reason"], "eod")
        self.assertAlmostEqual(r["exit"], 100.2 * (1 - 0.0005), places=2)

    def test_short_carries_a_borrow_fee_and_mirror_fills(self):
        plan = {"direction": "short", "entry": 100.0, "stop": 102.0,
                "target": 96.0, "qty": 10, "setup": "short_breakdown"}
        bars = _bars([(100, 100.5, 99.5, 100.0, 1000)] * 7 +
                     [(96, 96.5, 94.0, 95.0, 1000)] * 3)
        r = _simulate_exit(bars, 6, plan, 0.005, 5.0, borrow_bps_per_day=250.0)
        self.assertEqual(r["exit_reason"], "target")
        self.assertAlmostEqual(r["exit"], 96.0 * (1 + 0.0005), places=2)
        self.assertGreater(r["borrow_fee"], 0.0)
        self.assertGreater(r["net_pnl"], 0)

    def test_size_comes_from_the_plan_not_from_risk_math(self):
        """The backtest must not re-derive a size: that is the desk's job, and
        a second copy is how a backtest measures a strategy nobody runs."""
        plan = dict(self.PLAN, qty=3)
        bars = _bars([(100, 105, 99.5, 104.5, 1000)] * 9)
        r = _simulate_exit(bars, 6, plan, 0.005, 5.0)
        self.assertEqual(r["qty"], 3)

    def test_entry_before_the_opening_range_is_refused(self):
        # A bar inside the opening range has no OR to size against; the
        # simulation returns None rather than inventing levels.
        self.assertIsNone(_simulate_exit(_bars([(100, 101, 99, 100, 1000)] * 9),
                                         2, self.PLAN, 0.005, 5.0))


class StubbedOddsTests(unittest.TestCase):
    """The one gate that cannot be replayed must be visibly stubbed."""

    def test_stub_never_fails_closed(self):
        # `_validate_setup` blocks on odds["error"]; the stub carries no error
        self.assertFalse(BACKTEST_ODDS.get("error"))
        self.assertFalse(BACKTEST_ODDS["available"])
        self.assertIn("time machine", BACKTEST_ODDS["reason"])


class AggregationTests(unittest.TestCase):
    def _trade(self, direction, pnl):
        return {"direction": direction, "setup": "x", "net_pnl": pnl,
                "commission": 0.1, "borrow_fee": 0.0, "entry": 1.0,
                "exit": 2.0, "exit_reason": "target", "qty": 1, "notional": 1.0}

    def test_empty(self):
        out = _aggregate("X", [], lambda t: "RISK_ON")
        self.assertEqual(out["trades"], 0)

    def test_long_and_short_are_reported_separately(self):
        trades = [self._trade("long", 100), self._trade("long", -40),
                  self._trade("short", 60), self._trade("short", -20)]
        out = _aggregate("X", trades, lambda t: "RISK_OFF")
        self.assertEqual(out["longs"], 2)
        self.assertEqual(out["shorts"], 2)
        self.assertEqual(out["net_pnl"], 100.0)
        self.assertEqual(out["win_rate"], 0.5)
        # 100 + 60 wins against 40 + 20 losses: 160/60
        self.assertAlmostEqual(out["profit_factor"], 160.0 / 60.0, places=3)
        self.assertEqual(out["regimes"]["RISK_OFF"]["trades"], 4)

    def test_regime_split(self):
        trades = [self._trade("long", 10), self._trade("short", -5)]
        out = _aggregate("X", trades,
                         lambda t: "RISK_ON" if t["direction"] == "long" else "RISK_OFF")
        self.assertEqual(set(out["regimes"]), {"RISK_ON", "RISK_OFF"})
        self.assertEqual(out["regimes"]["RISK_OFF"]["net_pnl"], -5)


if __name__ == "__main__":
    unittest.main()
