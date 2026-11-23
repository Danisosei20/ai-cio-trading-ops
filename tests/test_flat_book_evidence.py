"""A flat book must still produce a fresh, honest simulation artifact.

Both book-dependent stages currently fail or go stale on every flat day,
which leaves the gate's evidence incomplete even though there are truly no
positions to score. The producers should write a dated empty snapshot.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import options_time_machine, scenario_analysis


class FlatBookEvidenceTests(unittest.TestCase):
    def test_scenario_analysis_writes_fresh_empty_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "scenario.json"
            with mock.patch.object(scenario_analysis, "OUT", out), \
                 mock.patch.object(scenario_analysis, "fetch_book", return_value=[]):
                rc = scenario_analysis.main([])
            self.assertEqual(rc, 0)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(doc["book"], [])
            self.assertEqual(doc["scenarios"], [])
            self.assertTrue(doc["generated_at"])
            self.assertIn("no open option positions", doc["reason"])

    def test_time_machine_writes_fresh_empty_snapshot_for_flat_book(self):
        class Backend:
            def __init__(self, *args, **kwargs):
                pass

            def list_positions(self):
                return []

        class Transport:
            @staticmethod
            def from_values(env):
                return object()

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "odds.json"
            with mock.patch.object(options_time_machine, "OUT", out), \
                 mock.patch("robinhood_tools.alpaca_paper.AlpacaPaperBackend", Backend), \
                 mock.patch("robinhood_tools.alpaca_paper.AlpacaPaperHttpTransport", Transport), \
                 mock.patch("robinhood_tools.settings.load_env", return_value={}):
                rc = options_time_machine.main(["--book", "--allow-stale"])
            self.assertEqual(rc, 0)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(doc["results"], [])
            self.assertTrue(doc["generated_at"])
            self.assertIn("no open option positions", doc["reason"])

    def test_time_machine_still_rejects_a_call_with_no_symbols_or_book(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "odds.json"
            with mock.patch.object(options_time_machine, "OUT", out):
                rc = options_time_machine.main([])
            self.assertEqual(rc, 2)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
