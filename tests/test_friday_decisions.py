"""The two owner decisions from the 2026-11-16 Friday review.

1. Weekend gap rule: no new short premium after Thursday 15:45 ET. SPY
   gapped through every -15% put stop on 2026-11-10; a Thursday-PM put
   cannot be exited until Monday's open, so the stop is fiction across
   the weekend.
2. Research-driven shorts: a bearish signal in options mode passes the
   regime gate when the tape is RISK_OFF - the same verdict the 15:10
   watch has always traded puts on. CHOP/UNKNOWN still block new risk.
"""
from __future__ import annotations

import ast
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from robinhood_tools.options_flow import weekend_gap_block
from scripts.desk_stage_agents import STAGES
from scripts.tradingagents_paper_auto import regime_gate_passes

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


class RegimeGateTest(unittest.TestCase):
    """The matrix, pinned: the new rule and everything it must not break."""

    def test_risk_on_passes_everything(self):
        for sig in ("buy", "overweight", "sell", "underweight", "hold"):
            for options in (False, True):
                self.assertTrue(regime_gate_passes(
                    regime="RISK_ON", circuit_broken=False, sig=sig,
                    options_mode=options))

    def test_bearish_options_pass_in_risk_off(self):
        # The 2026-11-16 decision: research-driven puts trade a down tape.
        for sig in ("sell", "underweight"):
            self.assertTrue(regime_gate_passes(
                regime="RISK_OFF", circuit_broken=False, sig=sig,
                options_mode=True))

    def test_buys_still_blocked_in_risk_off(self):
        for sig in ("buy", "overweight"):
            self.assertFalse(regime_gate_passes(
                regime="RISK_OFF", circuit_broken=False, sig=sig,
                options_mode=True),
                "a long premium buy must not open in a down tape")

    def test_chop_and_unknown_block_new_risk_but_not_exits(self):
        # Options mode: nothing new opens in CHOP/UNKNOWN (missing evidence
        # is not permission). Equity mode: a sell is an exit, and exits are
        # never blocked - that is the desk's standing rule.
        for regime in ("CHOP", "UNKNOWN"):
            for sig in ("buy", "overweight", "sell", "underweight"):
                self.assertFalse(regime_gate_passes(
                    regime=regime, circuit_broken=False, sig=sig,
                    options_mode=True))
            self.assertFalse(regime_gate_passes(
                regime=regime, circuit_broken=False, sig="buy",
                options_mode=False))
            self.assertTrue(regime_gate_passes(
                regime=regime, circuit_broken=False, sig="sell",
                options_mode=False),
                "an equity exit must never be regime-blocked")

    def test_equity_sells_are_exits_and_always_pass(self):
        for regime in ("RISK_OFF", "CHOP", "UNKNOWN"):
            self.assertTrue(regime_gate_passes(
                regime=regime, circuit_broken=False, sig="sell",
                options_mode=False),
                "an exit must never be blocked by the regime")

    def test_circuit_breaker_blocks_even_risk_on(self):
        self.assertFalse(regime_gate_passes(
            regime="RISK_ON", circuit_broken=True, sig="buy",
            options_mode=False))


class WeekendGapRuleTest(unittest.TestCase):
    """No new short premium after Thursday 15:45 ET; longs unaffected."""

    def test_thursday_after_cutoff_blocks_puts(self):
        block = weekend_gap_block(right="put", when=at(2026, 11, 19, 15, 50))
        self.assertIsNotNone(block)
        self.assertIn("weekend gap rule", block)

    def test_thursday_before_cutoff_allows_puts(self):
        self.assertIsNone(weekend_gap_block(right="put",
                                            when=at(2026, 11, 19, 10, 0)))

    def test_calls_are_unaffected(self):
        self.assertIsNone(weekend_gap_block(right="call",
                                            when=at(2026, 11, 19, 15, 50)))

    def test_other_weekdays_are_unaffected(self):
        for day in (16, 17, 18, 20):  # Mon, Tue, Wed, Fri 2026-11
            self.assertIsNone(weekend_gap_block(
                right="put", when=datetime(2026, 11, day, 16, 0,
                                           tzinfo=ET)))

    def test_the_opener_path_enforces_it(self):
        # place_long_option must consult the rule (source guard): the gate
        # is worthless if the flow never calls it.
        src = (ROOT / "robinhood_tools" / "options_flow.py").read_text(
            encoding="utf-8")
        tree = ast.parse(src)
        calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)]
        self.assertIn("weekend_gap_block", calls)


class MorningRunFlagsTest(unittest.TestCase):
    """The scheduled run must carry the flags the decision requires."""

    def test_morning_run_runs_options_with_the_watch_caps(self):
        # The scheduled 09:45 research run is defined by the orchestrator's
        # ResearchFleet stage, not by an inline command in the .bat.
        research = next(s for s in STAGES if s.agent == "ResearchFleet")
        flags = list(research.argv)
        for flag in ("--options", "--premium-cap", "3000",
                     "--max-option-positions", "5", "--ticker-timeout", "1200"):
            self.assertIn(flag, flags)


if __name__ == "__main__":
    unittest.main()