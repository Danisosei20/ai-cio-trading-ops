import unittest

from scripts.desk_stage_agents import STAGES, StageSpec, StageReport, normalize

SPEC = StageSpec(agent="Certifier", role="certify execution paths",
                 argv=("scripts/paper_certification.py",),
                 primary_artifact="outputs/certification/paper_certification.json",
                 required=True, allow_stale=False)


class Proc:
    returncode = 0
    stderr = ""


class NormalizeTests(unittest.TestCase):
    def test_ok_with_verdict_pass(self):
        r = normalize(SPEC, proc=Proc(), artifacts={SPEC.primary_artifact: {
            "verdict": "PASS", "counts": {"PASS": 70}}}, now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(r.status, "ok")
        self.assertIn("PASS", r.summary)
        self.assertEqual(r.metrics["verdict"], "PASS")

    def test_fail_when_artifact_missing(self):
        r = normalize(SPEC, proc=Proc(), artifacts={SPEC.primary_artifact: None},
                      now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(r.status, "fail")
        self.assertEqual(r.evidence, [])

    def test_nonzero_exit_is_fail(self):
        class Bad(Proc):
            returncode = 2

        r = normalize(SPEC, proc=Bad(), artifacts={SPEC.primary_artifact: {"verdict": "PASS"}},
                      now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(r.status, "fail")

    def test_as_dict_has_all_fields(self):
        r = normalize(SPEC, proc=Proc(), artifacts={SPEC.primary_artifact: {"verdict": "PASS"}},
                      now_iso="2026-11-21T10:20:00-05:00")
        self.assertIsInstance(r, StageReport)
        d = r.as_dict()
        for k in ("agent", "role", "status", "summary", "evidence", "metrics",
                  "started_at", "finished_at", "duration_seconds", "exit_code", "stderr_tail"):
            self.assertIn(k, d)


class RegistryTests(unittest.TestCase):
    def test_order_and_names(self):
        self.assertEqual([s.agent for s in STAGES], [
            "Reconciler", "ExitGuard", "OddsAnalyst", "ScenarioAnalyst",
            "PerformanceAnalyst", "Certifier", "Screener", "ZoneAnalyst",
            "ResearchFleet", "BarReader", "Reporter", "OrderAgent"])

    def test_research_is_dry_run_and_order_is_not(self):
        research = next(s for s in STAGES if s.agent == "ResearchFleet")
        order = next(s for s in STAGES if s.agent == "OrderAgent")
        self.assertIn("--dry-run", research.argv)
        self.assertNotIn("--dry-run", order.argv)

    def test_required_set_matches_spec(self):
        required = {s.agent for s in STAGES if s.required}
        self.assertEqual(required, {"Certifier", "Reconciler", "ExitGuard",
                                    "Screener", "ZoneAnalyst", "ResearchFleet", "BarReader"})

    def test_odds_stage_allows_stale(self):
        odds = next(s for s in STAGES if s.agent == "OddsAnalyst")
        self.assertTrue(odds.allow_stale)


if __name__ == "__main__":
    unittest.main()
