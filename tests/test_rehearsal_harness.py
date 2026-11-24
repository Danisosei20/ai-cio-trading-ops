"""Controlled, isolated 09:45 -> 10:20 execution rehearsal."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import rehearsal_harness as rh

BASE = dict(os.environ, EXECUTION_AUTH_HMAC_KEY="rehearsal-test-key-32-bytes-long",
            CIO_AUTH_ALLOW_EPHEMERAL_KEY="1",
            CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED="1",
            CIO_AUTH_ALLOW_PLAINTEXT_KEYSTORE="1")


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        self.env = dict(BASE, EXECUTION_AUTHORIZATION_ENABLED="true",
                        CIO_AUTH_KEYSTORE=str(self.root / "keys.json"))
        self.patcher = mock.patch.dict(os.environ, self.env, clear=True)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def test_proceed_scenario_issues_parent_and_consumes_children(self):
        result = rh.run_rehearsal(self.root, scenario="proceed")
        self.assertEqual(result["gate_verdict"], "proceed")
        self.assertTrue(result["parent_authorization"])
        self.assertTrue(result["long_entry"]["authorized"])
        self.assertTrue(result["short_entry"]["authorized"])
        self.assertTrue(result["option_entry"]["authorized"])
        self.assertFalse(result["replay_blocked"] is False)
        self.assertTrue(result["exits_exempt"])

    def test_halt_scenario_issues_no_authorization(self):
        result = rh.run_rehearsal(self.root, scenario="halt")
        self.assertEqual(result["gate_verdict"], "halt")
        self.assertIsNone(result["parent_authorization"])
        self.assertTrue(all(not result[k]["authorized"] for k in
                            ("long_entry", "short_entry", "option_entry")))

    def test_repeatability_identical_frozen_artifacts(self):
        a = rh.run_rehearsal(self.root, scenario="proceed")
        b = rh.run_rehearsal(self.root, scenario="proceed")
        self.assertEqual(a["gate_verdict"], b["gate_verdict"])
        self.assertEqual(a["evidence_hashes"], b["evidence_hashes"])

    def test_enforcement_flag_in_test_only_not_operational_default(self):
        from robinhood_tools.execution_authorization import enforcement_enabled
        self.assertTrue(enforcement_enabled())  # isolated process only
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(enforcement_enabled())

    def test_report_is_written(self):
        rh.run_rehearsal(self.root, scenario="proceed")
        report = self.root / "rehearsal_result.json"
        self.assertTrue(report.exists())
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertIn("gate_verdict", data)


if __name__ == "__main__":
    unittest.main()
