"""The intraday order path must honour today's recorded go/no-go verdict."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.intraday_loop import desk_verdict

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 23, 10, 30, tzinfo=ET)


class VerdictGateTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def _write(self, verdict, stamp="2026-11-23T09:45:00-05:00"):
        p = self.root / "outputs/orchestrator/latest.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"run_id": "r1", "verdict": verdict,
                                 "generated_at": stamp}), encoding="utf-8")

    def test_proceed_today_allows(self):
        self._write("proceed")
        self.assertEqual(desk_verdict(self.root, now=NOW), (True, "proceed"))

    def test_halt_blocks(self):
        self._write("halt")
        allowed, why = desk_verdict(self.root, now=NOW)
        self.assertFalse(allowed)
        self.assertIn("halt", why)

    def test_review_blocks(self):
        self._write("review")
        self.assertFalse(desk_verdict(self.root, now=NOW)[0])

    def test_stale_decision_blocks(self):
        self._write("proceed", "2026-11-20T09:45:00-05:00")
        allowed, why = desk_verdict(self.root, now=NOW)
        self.assertFalse(allowed)
        self.assertIn("not today", why)

    def test_missing_decision_blocks(self):
        allowed, why = desk_verdict(self.root, now=NOW)
        self.assertFalse(allowed)
        self.assertTrue(why)

    def test_malformed_decision_blocks(self):
        p = self.root / "outputs/orchestrator/latest.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not-json", encoding="utf-8")
        self.assertFalse(desk_verdict(self.root, now=NOW)[0])

    def test_missing_timestamp_blocks(self):
        p = self.root / "outputs/orchestrator/latest.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"verdict": "proceed"}), encoding="utf-8")
        self.assertFalse(desk_verdict(self.root, now=NOW)[0])


if __name__ == "__main__":
    unittest.main()
