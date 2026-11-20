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
    reward_risk_ratio,
)


class HorizonTest(unittest.TestCase):
    def test_horizon_is_min_of_dte_and_cap(self):
        self.assertEqual(horizon_for("2027-01-15", date(2026, 11, 16)), 10)
        self.assertEqual(horizon_for("2026-11-20", date(2026, 11, 16)), 4)
        self.assertEqual(horizon_for("2026-11-16", date(2026, 11, 16)), 1)

    def test_unreadable_expiry_is_one_day_not_a_crash(self):
        self.assertEqual(horizon_for("not-a-date", date(2026, 11, 16)), 1)


class VerdictMathTest(unittest.TestCase):
    def test_reward_risk_ratio_requires_two_to_one(self):
        self.assertEqual(reward_risk_ratio(0.30, 0.15), 2.0)
        self.assertLess(reward_risk_ratio(0.20, 0.15), 2.0)

    def test_entry_verdict_rejects_below_two_to_one_model(self):
        v = entry_odds_verdict(premium0=10.0, delta=-0.5, spot0=95.0,
                               sigma=0.9, expiry="2027-01-15",
                               today=date(2026, 11, 16), paths=1000,
                               min_ev_pct=0.0, modeled_reward_pct=0.20)
        self.assertFalse(v["approved"])
        self.assertIn("reward/risk", v["reason"])

    def test_default_model_matches_guard_stop_policy(self):
        v = entry_odds_verdict(premium0=10.0, delta=-0.5, spot0=95.0,
                               sigma=0.9, expiry="2027-01-15",
                               today=date(2026, 11, 16), paths=1000,
                               min_ev_pct=0.0)
        self.assertEqual(v["modeled_reward_pct"], 0.50)
        self.assertEqual(v["reward_risk"], 2.0)

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
        an uncapped win is positive EV by construction. At min_ev_pct=0
        the gate therefore approves nearly everything; it earns its keep
        by failing closed on missing vol and by catching the +2% scalp if
        that policy ever returns. (A tiny delta with tiny vol prices
        ~zero EV - indistinguishable from no edge either way - which is
        why that case is not in the assertion set.)

        Owner decision 2026-11-17 moved the default to +10%, described
        everywhere as a VOLUME THROTTLE and never as an edge filter - see
        the comment on DEFAULT_MIN_EV_PCT and the pairing test below.
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

    def test_ev_does_not_rank_contracts_by_moneyness(self):
        """The margin is a throttle, not a selector - pinned so it is never
        mistaken for one.

        Measured 2026-11-17: over 50 contracts (10 moneyness levels x 5
        vol levels) EV was positive in every case and the spread across
        moneyness was small - a contract struck 30% out of the money
        scored within a couple of points of an at-the-money one, and the
        ordering inverted depending on vol. The number tracks volatility,
        not contract quality, so a threshold derived from it rejects the
        low-vol tail rather than the bad trades.

        If this assertion ever fails because EV started ordering cleanly
        by moneyness, the model changed - and that is worth a human look
        before anyone leans on the number harder.
        """
        spot = 100.0
        cases = [
            ("deep OTM call", 0.05, 0.60),
            ("near ATM call", 0.52, 5.20),
            ("ITM call", 0.72, 9.40),
        ]
        evs = {}
        for label, delta, premium in cases:
            v = entry_odds_verdict(premium0=premium, delta=delta, spot0=spot,
                                   sigma=0.55, expiry="2027-01-15",
                                   today=date(2026, 11, 16), paths=1000,
                                   min_ev_pct=0.0)
            evs[label] = float(v["ev_pct_on_premium"])

        # Every plausible contract prices positive.
        for label, ev in evs.items():
            self.assertGreater(ev, 0.0, f"{label} priced EV {ev:+.2%}")
        # And the moneyness spread is small enough that ordering is noise.
        spread = max(evs.values()) - min(evs.values())
        self.assertLess(spread, 0.15,
                        f"EV now separates moneyness by {spread:+.1%} - the "
                        f"model's behaviour changed, re-read it before "
                        f"treating the bar as selection: {evs}")

    def test_no_vol_estimate_fails_closed(self):
        q = self._quote(iv=None)
        with self.assertRaises(PolicyViolation) as ctx:
            require_positive_ev(quote=q, today=date(2026, 11, 16), paths=100,
                                sigma_provider=lambda underlying, iv: None)
        self.assertIn("fail closed", str(ctx.exception))

    def test_the_provider_is_asked_for_vol_when_sigma_is_absent(self):
        """Plumbing: the vol provider is consulted when sigma is absent.

        min_ev_pct is pinned to 0 here on purpose. This contract prices
        about +9% EV under a 200-path run, which the 2026-11-17 default
        bar of +10% refuses - and that refusal would mask the one thing
        this test is about. The bar's own behaviour is pinned by
        test_a_marginal_contract_is_refused_by_the_bar and by
        test_ev_does_not_rank_contracts_by_moneyness.
        """
        asked = []

        def provider(underlying, iv):
            asked.append(underlying)
            return 0.9

        v = require_positive_ev(quote=self._quote(), today=date(2026, 11, 16),
                                paths=200, sigma_provider=provider,
                                min_ev_pct=0.0)
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
