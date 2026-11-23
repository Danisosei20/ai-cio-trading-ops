"""Failed research must never count as healthy evidence for the gate."""
import unittest

from scripts.desk_stage_agents import STAGES, StageSpec, artifact_error, normalize
from scripts.desk_orchestrator import run_slot


class Proc:
    returncode = 0
    stdout = ""
    stderr = ""


class _Root:
    pass


class ResearchEvidenceTests(unittest.TestCase):
    def test_artifact_error_detects_reason_prefix(self):
        self.assertIn("ModuleNotFound", artifact_error(
            {"action": "skip", "reason": "error: ModuleNotFoundError: tradingagents"}))

    def test_artifact_error_detects_error_key(self):
        self.assertEqual(artifact_error({"error": "bad"}), "bad")

    def test_healthy_artifact_has_no_error(self):
        self.assertEqual(artifact_error({"action": "buy_placed", "reason": "ok"}), "")

    def test_error_artifact_fails_the_stage(self):
        spec = StageSpec("ResearchFleet", "research", ("x.py",), "a.json", True, False)
        r = normalize(spec, proc=Proc(),
                      artifacts={"a.json": {"reason": "error: no tradingagents"}},
                      now_iso="2026-11-23T09:45:00-05:00")
        self.assertEqual(r.status, "fail")
        self.assertIn("artifact error", r.summary)

    def test_ok_artifact_still_passes(self):
        spec = StageSpec("ResearchFleet", "research", ("x.py",), "a.json", True, False)
        r = normalize(spec, proc=Proc(),
                      artifacts={"a.json": {"action": "buy_placed", "generated_at": "2026-11-23T09:00:00-05:00"}},
                      now_iso="2026-11-23T09:45:00-05:00")
        self.assertEqual(r.status, "ok")

    def test_research_stages_select_llm_interpreter(self):
        names = {s.agent: s.python for s in STAGES}
        self.assertEqual(names.get("ResearchFleet"), "tradingagents")
        self.assertEqual(names.get("OrderAgent"), "tradingagents")
        self.assertEqual(names.get("BarReader"), "")

    def test_llm_interpreter_is_used_when_configured(self):
        import os
        import tempfile
        from unittest import mock
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as td:
            root = P(td)
            ta = root / "fake-tradingagents-python.exe"
            ta.write_text("", encoding="utf-8")
            env = dict(os.environ, TRADINGAGENTS_VENV_PYTHON=str(ta))
            seen = []
            class Proc:
                returncode = 0
                stdout = ""
                stderr = ""
            def fake(argv, **kw):
                seen.append(argv)
                return Proc()
            with mock.patch.dict(os.environ, env, clear=True):
                run_slot(root, "interp-probe", slot="0945", exec_cmd=fake,
                         now_iso="2026-11-23T09:45:00-05:00")
            research = [a for a in seen if any("paper_auto" in str(x) for x in a)]
            self.assertTrue(research)
            self.assertEqual(research[0][0], str(ta))


if __name__ == "__main__":
    unittest.main()
