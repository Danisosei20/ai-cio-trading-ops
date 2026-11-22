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

    def _write_raw(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _seed_time_machine(self, results=None):
        self._write(TIME_MACHINE_REL, {
            "generated_at": "2026-11-21T09:05:00-05:00",
            "results": [_result()] if results is None else results,
        })

    def _seed_gate(self, candidates=None):
        self._write(GATE_REL, {
            "generated_at": "2026-11-21T09:05:00-05:00",
            "candidates": [] if candidates is None else candidates,
        })

    def test_unavailable_on_missing_artifacts(self):
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "unavailable")

    def test_unavailable_on_malformed_artifact(self):
        self._write(TIME_MACHINE_REL, {"results": "not-a-list"})
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "unavailable")

    def test_candidates_from_real_artifact(self):
        self._seed_time_machine()
        self._seed_gate()
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["candidates"]), 1)
        cand = payload["candidates"][0]
        self.assertEqual(cand["strike"], 145.0)
        self.assertEqual(cand["premium"], 22.55)
        self.assertEqual(cand["delta"], -0.6131)
        self.assertEqual(cand["ev_pct"], 0.06673)
        self.assertEqual(cand["ev_usd"], 150.14)
        self.assertIn(TIME_MACHINE_REL, cand["source"])
        self.assertIn(GATE_REL, " ".join(payload["sources"]))

    def test_rejections_are_reported(self):
        self._seed_time_machine()
        self._seed_gate([
            {"symbol": "AES", "side": "call", "error": "PolicyViolation: no edge"},
        ])
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["rejected"]), 1)
        row = payload["rejected"][0]
        self.assertEqual(row["underlying"], "AES")
        self.assertEqual(row["right"], "call")
        self.assertIn("PolicyViolation", row["reason"])
        self.assertIn(GATE_REL, row["source"])

    def test_policy_rejection_is_reported(self):
        """Real producer shape: entry_odds_verdict() refusal, not an error key."""
        self._seed_time_machine()
        self._seed_gate([
            {"symbol": "AES", "side": "call", "contract": "AES260116C00020000",
             "premium": 0.42, "delta": 0.5, "sigma": 0.31,
             "approved": False,
             "reason": "simulated EV -3.20% on premium over 10000 paths "
                       "(stop 0% / neither 0%) below the +10.00% bar"},
        ])
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["rejected"]), 1)
        row = payload["rejected"][0]
        self.assertEqual(row["underlying"], "AES")
        self.assertEqual(row["right"], "call")
        self.assertEqual(
            row["reason"],
            "simulated EV -3.20% on premium over 10000 paths "
            "(stop 0% / neither 0%) below the +10.00% bar",
        )
        self.assertIn(GATE_REL, row["source"])

    def test_approved_verdict_is_not_a_rejection(self):
        self._seed_time_machine()
        self._seed_gate([
            {"symbol": "AAPL", "side": "call", "contract": "AAPL260116C00145000",
             "approved": True,
             "reason": "simulated EV +12.00% on premium over 10000 paths "
                       "meets the +10.00% bar"},
        ])
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["rejected"], [])

    def test_unavailable_when_gate_artifact_missing(self):
        self._seed_time_machine()
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("reason", payload)

    def test_unavailable_when_gate_artifact_unparsable(self):
        self._seed_time_machine()
        self._write_raw(GATE_REL, "{not json")
        payload = option_candidates(self.root)
        self.assertEqual(payload["status"], "unavailable")

    def test_every_row_has_source(self):
        self._seed_time_machine()
        self._seed_gate([
            {"symbol": "AES", "side": "call", "error": "PolicyViolation: no edge"},
            {"symbol": "AMD", "side": "put", "approved": False,
             "reason": "simulated EV -1.00% below the +10.00% bar"},
        ])
        payload = option_candidates(self.root)
        for row in payload["candidates"] + payload["rejected"]:
            self.assertIsInstance(row["source"], str)
            self.assertTrue(row["source"].strip(), row)

    def test_finite_numbers_and_text(self):
        self._seed_time_machine([
            _result(),
            _result(symbol="BAD-NAN", delta=float("nan")),
            _result(symbol="BAD-INF", premium0="inf"),
            _result(symbol="BAD-TEXT", strike="not-a-number"),
        ])
        self._seed_gate()
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
        source = Path("scripts/agent_office.py").read_text(encoding="utf-8")
        self.assertIn('elif route == "/api/options":', source)
        # A comment in agent_office.py once satisfied the old '"/options"' string
        # assertion. Require the real dispatch line so the route cannot be faked.
        self.assertIn('elif route == "/options":', source)
        self.assertNotIn("do_POST", source)


if __name__ == "__main__":
    unittest.main()
