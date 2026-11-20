"""Portfolio scenario analysis: deterministic P/L for any market move.

Runs the pure analyse() over a stubbed book, so the Greek arithmetic and
the stop-trigger logic are pinned without the broker. Live use:
`python scripts/scenario_analysis.py` -> outputs/simulation/
scenario_analysis.json.
"""
from __future__ import annotations

import unittest

from scripts.scenario_analysis import STOP_PCT, analyse


def book(qty=2, entry=11.00, mark=10.05, spot=96.12, delta=-0.4788,
         gamma=0.02, theta=-0.05, vega=0.30, iv=0.60):
    return [{"symbol": "RDDT270115P00095000", "underlying": "RDDT",
             "right": "put", "expiry": "2027-01-15", "qty": qty,
             "entry": entry, "mark": mark, "spot": spot,
             "greeks": {"delta": delta, "gamma": gamma, "theta": theta,
                        "vega": vega, "iv": iv}}]


class ScenarioAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.rows = {r["scenario"]: r for r in analyse(book())}

    def test_a_flat_tape_prices_the_current_pnl(self):
        flat = self.rows["flat"]
        # (10.05 - 11.00) x 2 x 100
        self.assertEqual(flat["book_pl_vs_entry"], -190.0)
        self.assertEqual(flat["book_change"], 0.0)

    def test_a_put_gains_when_the_underlier_falls(self):
        crash = self.rows["underlier -20% (crash)"]
        rally = self.rows["underlier +20% (melt-up)"]
        self.assertGreater(crash["book_change"], 0)
        self.assertLess(rally["book_change"], 0)

    def test_decay_hurts_a_long_option(self):
        for name in ("1 day of decay", "5 days of decay", "10 days of decay"):
            self.assertLess(self.rows[name]["book_change"], 0, name)

    def test_iv_spike_helps_a_long_option(self):
        self.assertGreater(self.rows["IV +20% (spike)"]["book_change"], 0)
        self.assertLess(self.rows["IV -20% (calm)"]["book_change"], 0)

    def test_the_stop_triggers_only_when_the_band_is_hit(self):
        # A +20% underlier move drives this put past -15% of premium.
        big = next(r for r in analyse(book()) if r["d_spot_pct"] == 0.20)
        self.assertTrue(big["stop_triggered"],
                        "a +20% underlier move must trip the -15% stop")
        self.assertTrue(any("STOP" in t for t in big["triggers"]))
        # And a mild move must not.
        mild = self.rows["underlier +2%"]
        self.assertFalse(mild["stop_triggered"])

    def test_every_scenario_reports_a_number(self):
        for row in analyse(book()):
            self.assertIn("book_pl_vs_entry", row)
            self.assertIn("positions", row)
            self.assertEqual(len(row["positions"]), 1)

    def test_the_stop_threshold_is_the_guards(self):
        self.assertEqual(STOP_PCT, -0.15)


if __name__ == "__main__":
    unittest.main()