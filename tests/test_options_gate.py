"""The options-entry odds gate (owner decision 2026-11-16).

The equity day-trade path has gated entries on the Time Machine all along;
the options path - where the desk's losses are - had none. This gate
simulates the candidate contract under the guard's exit bands and refuses
openers whose expected value on premium is negative, failing closed when
no vol estimate exists (missing evidence is not permission to trade).
"""
from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from datetime import date
from decimal import Decimal
from pathlib import Path

from robinhood_tools.errors import PolicyViolation
from robinhood_tools.options_gate import (
    entry_odds_verdict,
    horizon_for,
    require_positive_ev,
)


class HorizonTest(unittest.TestCase):
    def test_horizon_is_min_of_dte_and_cap(self):
        self.assertEqual(horizon_for("2027-01-15", date(2026, 11, 16)), 10)
        self.assertEqual(horizon_for("2026-11-20", date(2026, 11, 16)), 4)
        self.assertEqual(horizon_for("2026-11-16", date(2026, 11, 16)), 1)

    def test_unreadable_expiry_is_one_day_not_a_crash(self):
        self.assertEqual(horizon_for("not-a-date", date(2026, 11, 16)), 1)


class VerdictMathTest(unittest.TestCase):
    def test_positive_ev_approves_with_numbers_attached(self):
        v = entry_odds_verdict(premium0=10.0, delta=-0.5, spot0=95.0,
                               sigma=0.9, expiry="2027-01-15",
                               today=date(2026, 11, 16), paths=1000,
                               min_ev_pct=0.0)
        self.assertTrue(v["approved"])
        self.assertIn("meets", v["reason"])
        self.assertIn("stop", v["reason"])

    def test_a_high_bar_refuses_a_marginal_contract(self):
        v = entry_odds_verdict(premium0=10.0, delta=-0.5, spot0=95.0,
                               sigma=0.9, expiry="2027-01-15",
                               today=date(2026, 11, 16), paths=1000,
                               min_ev_pct=0.50)
        self.assertFalse(v["approved"])
        self.assertIn("below", v["reason"])

    def test_the_scalp_policy_is_negative_ev_so_a_marginal_bar_refuses(self):
        # With an explicit +2% target (the reverted policy) the same
        # contract prices negative; the gate must refuse it.
        v = entry_odds_verdict(premium0=10.0, delta=-0.5, spot0=95.0,
                               sigma=0.9, expiry="2027-01-15",
                               today=date(2026, 11, 16), paths=1000,
                               target_pct=0.02, min_ev_pct=0.0)
        self.assertLess(v["ev_pct_on_premium"], 0.0)
        self.assertFalse(v["approved"])


class RequirePositiveEvTest(unittest.TestCase):
    """The gate as the flow calls it: stubbed quote + injected sigma."""

    def _quote(self, iv=None, delta="-0.4788"):
        from robinhood_tools.options import OptionContract, OptionQuote

        contract = OptionContract("RDDT", "2027-01-15", Decimal("95.00"),
                                  "put", "RDDT270115P00095000")
        return OptionQuote(contract=contract, bid=Decimal("10.20"),
                           ask=Decimal("10.36"), underlying_price=Decimal("96.12"),
                           quoted_at="2026-11-16T20:00:00Z",
                           iv=Decimal(str(iv)) if iv else None,
                           delta=Decimal(delta))

    def test_a_marginal_contract_is_refused_by_the_bar(self):
        # Cheap premium, tiny delta: the runner payoff barely clears the
        # stop, so the simulated EV sits just above zero.
        q = self._quote(delta="-0.20")
        with self.assertRaises(PolicyViolation) as ctx:
            require_positive_ev(quote=q, today=date(2026, 11, 16), paths=1000,
                                sigma=0.60, min_ev_pct=0.05)
        self.assertIn("odds gate", str(ctx.exception))
        self.assertIn("refused", str(ctx.exception))

    def test_the_bar_is_near_a_no_op_under_winners_run(self):
        """Honest finding, pinned so it cannot be forgotten.

        With the owner's 2026-11-16 policy (no take-profit, -15% stop,
        +25% trail) the simulated EV is POSITIVE across every plausible
        contract in a drift-less delta-only walk - a capped loss against
        an uncapped win is positive EV by construction. The gate at
        min_ev_pct=0 therefore approves nearly everything; it earns its
        keep by failing closed on missing vol and by catching the +2%
        scalp if that policy ever returns. (A tiny delta with tiny vol
        prices ~zero EV - indistinguishable from no edge either way -
        which is why that case is not in the assertion set.) A margin
        (min_ev_pct>0) is the only version that filters contracts, and
        that is the owner's parameter to set.
        """
        for delta, sigma in (("-0.4788", 0.92), ("-0.20", 0.60), ("-0.60", 1.20)):
            with self.subTest(delta=delta, sigma=sigma):
                v = entry_odds_verdict(premium0=10.0, delta=float(delta),
                                       spot0=95.0, sigma=sigma,
                                       expiry="2027-01-15",
                                       today=date(2026, 11, 16), paths=1000,
                                       min_ev_pct=0.0)
                self.assertTrue(v["approved"],
                                f"delta {delta} sigma {sigma} priced "
                                f"EV {v['ev_pct_on_premium']:+.2%}")

    def test_no_vol_estimate_fails_closed(self):
        q = self._quote(iv=None)
        with self.assertRaises(PolicyViolation) as ctx:
            require_positive_ev(quote=q, today=date(2026, 11, 16), paths=100,
                                sigma_provider=lambda underlying, iv: None)
        self.assertIn("fail closed", str(ctx.exception))

    def test_the_provider_is_asked_for_vol_when_sigma_is_absent(self):
        asked = []

        def provider(underlying, iv):
            asked.append(underlying)
            return 0.9

        v = require_positive_ev(quote=self._quote(), today=date(2026, 11, 16),
                                paths=200, sigma_provider=provider)
        self.assertEqual(asked, ["RDDT"])
        self.assertIn("approved", v)


class SigmaForUnderlierTest(unittest.TestCase):
    """The vol fetcher is the one network edge; it must be covered.

    2026-11-16: the first live dry-run of the gate failed with
    ModuleNotFoundError because this helper imported from a module path
    that does not exist - and every unit test passed, because they all
    inject sigma and never call it. This closes that gap.
    """

    def test_falls_back_to_quoted_iv_when_history_is_missing(self):
        from robinhood_tools.options_gate import sigma_for_underlier

        with unittest.mock.patch("yfinance.Ticker") as ticker:
            ticker.return_value.history.return_value = None
            sigma = sigma_for_underlier("NVDA", 0.65)
        self.assertEqual(sigma, 0.65)

    def test_returns_none_when_neither_source_exists(self):
        from robinhood_tools.options_gate import sigma_for_underlier

        with unittest.mock.patch("yfinance.Ticker") as ticker:
            ticker.return_value.history.side_effect = RuntimeError("no net")
            sigma = sigma_for_underlier("NVDA", None)
        self.assertIsNone(sigma)

    def test_realized_vol_wins_when_history_exists(self):
        import pandas as pd

        from robinhood_tools.options_gate import sigma_for_underlier

        closes = [100.0 + (i % 5) for i in range(40)]
        frame = pd.DataFrame({"Close": closes})
        with unittest.mock.patch("yfinance.Ticker") as ticker:
            ticker.return_value.history.return_value = frame
            sigma = sigma_for_underlier("NVDA", 0.65)
        self.assertIsNotNone(sigma)
        self.assertGreater(sigma, 0.0)
        self.assertNotEqual(sigma, 0.65)


class PlacementWiringTest(unittest.TestCase):
    """place_long_option must consult the gate; close_long_option must not."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "cio.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_the_opener_calls_the_gate_and_the_closer_does_not(self):
        import ast

        src = (Path(__file__).resolve().parents[1]
               / "robinhood_tools" / "options_flow.py").read_text(
                   encoding="utf-8")
        tree = ast.parse(src)
        calls: dict[str, list[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                names = {n.func.id for n in ast.walk(node)
                         if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name)}
                calls[node.name] = sorted(names)
        self.assertIn("require_positive_ev", calls.get("place_long_option", []))
        self.assertNotIn("require_positive_ev",
                         calls.get("close_long_option", []),
                         "an exit must never be odds-gated")


if __name__ == "__main__":
    unittest.main()