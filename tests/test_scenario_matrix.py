"""The scenario matrix must stay green and keep covering the desk's claims.

The unit suite proves each gate; the certification proves each execution
path; scripts/scenario_test.py answers the operator's question - "if the
tape is X, what does the desk DO?" This runs that harness in CI (writing
to a temp path, never the repo artifact) and fails on any regression,
including a group crashing before it can report.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import scenario_test


class ScenarioMatrixTest(unittest.TestCase):
    def test_every_scenario_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "scenarios.json"
            scenario_test.results.clear()  # the module accumulates rows
            with patch.object(scenario_test, "OUT", out):
                code = scenario_test.main([])
                self.assertEqual(code, 0,
                                 "the scenario matrix reported failures - "
                                 "see stdout")
                self.assertTrue(out.exists())
                payload = __import__("json").loads(out.read_text(encoding="utf-8"))
        self.assertGreaterEqual(payload["passed"], 30,
                                "the matrix must cover the desk's decision "
                                "surface, not a token few")
        self.assertEqual(payload["failed"], 0)

    def test_the_matrix_covers_every_gate_family(self):
        groups = set()
        scenario_test.results.clear()
        for group in (scenario_test.scenarios_regime,
                      scenario_test.scenarios_weekend,
                      scenario_test.scenarios_loss_memory,
                      scenario_test.scenarios_odds_gate,
                      scenario_test.scenarios_exit_policy,
                      scenario_test.scenarios_entry_caps,
                      scenario_test.scenarios_ledger,
                      scenario_test.scenarios_controls,
                      scenario_test.scenarios_entry_funnel,
                      scenario_test.scenarios_fail_closed,
                      scenario_test.scenarios_recon):
            group()
            for row in scenario_test.results:
                groups.add(row["group"])
        expected = {"regime", "weekend-gap", "loss-memory", "odds-gate",
                    "exit-policy", "entry-caps", "ledger", "controls",
                    "entry-funnel", "fail-closed", "broker-recon"}
        self.assertTrue(expected.issubset(groups),
                        f"missing families: {expected - groups}")


if __name__ == "__main__":
    unittest.main()