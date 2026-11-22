"""Read-only, fail-closed assembly of the desk's option candidates.

The viewer must never invent a candidate: every row carries the artifact that
produced it, bad numeric rows are dropped, and a broken primary artifact fails
closed to ``unavailable``.
"""
import json
import math
import tempfile
import unittest
from pathlib import Path

from scripts.options_candidates import option_candidates

TIME_MACHINE_REL = "outputs/simulation/options_time_machine.json"
GATE_REL = "outputs/simulation/options_gate_dryrun.json"


def _result(**overrides):
    """One fully-shaped time-machine result, matching the producer contract."""
    row = {
        "symbol": "AAPL 260116C00145000", "underlying": "AAPL", "right": "C",
        "strike": "145.0", "expiry": "2026-01-16", "dte": 55,
        "premium0": 22.55, "delta": -0.6131,
        "ev_pct_on_premium": 0.06673, "ev_usd_per_contract": 150.14,
        "target_first": 0.55, "stop_first": 0.20,
        "generated_at": "2026-11-21T09:00:00-05:00",
    }
    row.update(overrides)
    return row


class OptionCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def _write(self, rel, doc):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def test_unavailable_on_missing_artifacts(self):
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "unavailable")

    def test_unavailable_on_malformed_artifact(self):
        self._write(TIME_MACHINE_REL, {"results": "not-a-list"})
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "unavailable")

    def test_candidates_from_real_artifact(self):
        self._write(TIME_MACHINE_REL, {
            "generated_at": "2026-11-21T09:05:00-05:00",
            "results": [_result()],
        })
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["candidates"]), 1)
        cand = payload["candidates"][0]
        self.assertEqual(cand["strike"], 145.0)
        self.assertEqual(cand["premium"], 22.55)
        self.assertEqual(cand["delta"], -0.6131)
        self.assertEqual(cand["ev_pct"], 0.06673)
        self.assertIn(TIME_MACHINE_REL, cand["source"])

    def test_rejections_are_reported(self):
        self._write(GATE_REL, {"candidates": [
            {"symbol": "AES", "side": "call", "error": "PolicyViolation: no edge"},
        ]})
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["rejected"]), 1)
        row = payload["rejected"][0]
        self.assertEqual(row["underlying"], "AES")
        self.assertEqual(row["right"], "call")
        self.assertIn("PolicyViolation", row["reason"])
        self.assertIn(GATE_REL, row["source"])

    def test_every_row_has_source(self):
        self._write(TIME_MACHINE_REL, {
            "generated_at": "2026-11-21T09:05:00-05:00", "results": [_result()],
        })
        self._write(GATE_REL, {"candidates": [
            {"symbol": "AES", "side": "call", "error": "PolicyViolation: no edge"},
        ]})
        payload = option_candidates(self.root)
        for row in payload["candidates"] + payload["rejected"]:
            self.assertIsInstance(row["source"], str)
            self.assertTrue(row["source"].strip(), row)

    def test_finite_numbers_and_text(self):
        self._write(TIME_MACHINE_REL, {
            "generated_at": "2026-11-21T09:05:00-05:00",
            "results": [
                _result(),
                _result(symbol="BAD-NAN", delta=float("nan")),
                _result(symbol="BAD-INF", premium0="inf"),
                _result(symbol="BAD-TEXT", strike="not-a-number"),
            ],
        })
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["candidates"]), 1)
        cand = payload["candidates"][0]
        self.assertIsInstance(cand["contract"], str)
        numeric = ("strike", "dte", "premium", "delta", "ev_pct", "ev_usd",
                   "target_first", "stop_first")
        for key in numeric:
            self.assertTrue(math.isfinite(cand[key]), key)

    def test_read_only_options_routes(self):
        source = (Path("scripts/agent_office.py")).read_text(encoding="utf-8")
        self.assertIn('"/api/options"', source)
        self.assertIn('"/options"', source)
        self.assertNotIn("do_POST", source)


if __name__ == "__main__":
    unittest.main()
