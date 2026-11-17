"""The options Time Machine: seeded odds for the desk's exit bands.

Live result on the desk's own book (2026-11-16, RDDT270115P00095000,
60 DTE, premium $10.28, sigma 0.92, 20k paths): target-first 61.0%,
stop-first 39.0%, neither 0.0%, EV -4.63% on premium = -$47.53 per
contract per round trip. The +2% take-profit / -15% stop policy does not
pay for its own loss size on a high-vol name under a random walk - which
is why these tests pin the math and the model's limits, not a promise.
"""
from __future__ import annotations

import unittest

from scripts.options_time_machine import (
    annualized_vol,
    simulate_option_odds,
)


class SimulateOptionOddsTest(unittest.TestCase):
    def test_same_seed_same_answer(self):
        kwargs = dict(premium0=10.0, delta=-0.5, spot0=95.0, sigma=0.9,
                      days=10, paths=500, target_pct=0.02, stop_pct=0.15)
        first = simulate_option_odds(seed=11, **kwargs)
        second = simulate_option_odds(seed=11, **kwargs)
        self.assertEqual(first, second)

    def test_zero_vol_never_crosses_a_band(self):
        odds = simulate_option_odds(premium0=10.0, delta=-0.5, spot0=95.0,
                                    sigma=0.0, days=10, paths=200,
                                    target_pct=0.02, stop_pct=0.15, seed=3)
        self.assertEqual(odds["target_first"], 0.0)
        self.assertEqual(odds["stop_first"], 0.0)
        self.assertEqual(odds["neither"], 1.0)

    def test_probabilities_sum_to_one(self):
        for sigma in (0.2, 0.9, 2.5):
            with self.subTest(sigma=sigma):
                odds = simulate_option_odds(premium0=10.0, delta=-0.5,
                                            spot0=95.0, sigma=sigma, days=10,
                                            paths=500, target_pct=0.02,
                                            stop_pct=0.15, seed=5)
                total = (odds["target_first"] + odds["stop_first"]
                         + odds["neither"])
                self.assertAlmostEqual(total, 1.0, places=6)

    def test_the_tighter_band_resolves_first_often(self):
        # A +2% band is far closer than -15%: with real vol most paths
        # touch the target first. This is the policy's actual shape.
        odds = simulate_option_odds(premium0=10.0, delta=-0.5, spot0=95.0,
                                    sigma=0.9, days=10, paths=2000,
                                    target_pct=0.02, stop_pct=0.15, seed=7)
        self.assertGreater(odds["target_first"], odds["stop_first"])

    def test_ev_is_the_policy_payoff_matrix(self):
        odds = simulate_option_odds(premium0=10.0, delta=-0.5, spot0=95.0,
                                    sigma=0.9, days=10, paths=2000,
                                    target_pct=0.02, stop_pct=0.15, seed=7)
        expected = (odds["target_first"] * 0.02
                    - odds["stop_first"] * 0.15)
        self.assertAlmostEqual(odds["ev_pct_on_premium"], expected, places=4)
        self.assertAlmostEqual(odds["ev_usd_per_contract"],
                               round(expected * 10.0 * 100, 2), places=1)
        # and the live-shape claim: a 61/39 split at 1:7.5 pays is negative.
        self.assertLess(odds["ev_pct_on_premium"], 0.0)

    def test_a_wide_band_pair_survives_more_paths(self):
        tight = simulate_option_odds(premium0=10.0, delta=-0.5, spot0=95.0,
                                     sigma=0.5, days=5, paths=500,
                                     target_pct=0.02, stop_pct=0.15, seed=9)
        wide = simulate_option_odds(premium0=10.0, delta=-0.5, spot0=95.0,
                                    sigma=0.5, days=5, paths=500,
                                    target_pct=0.30, stop_pct=0.30, seed=9)
        self.assertGreater(wide["neither"], tight["neither"])


class AnnualizedVolTest(unittest.TestCase):
    def test_too_little_data_returns_none(self):
        self.assertIsNone(annualized_vol([100.0, 101.0, 102.0]))

    def test_a_trending_series_gives_positive_vol(self):
        closes = [100.0 + i * 0.5 for i in range(40)]
        vol = annualized_vol(closes)
        self.assertIsNotNone(vol)
        self.assertGreater(vol, 0.0)

    def test_a_flat_series_gives_zero_vol(self):
        self.assertEqual(annualized_vol([100.0] * 40), 0.0)


if __name__ == "__main__":
    unittest.main()