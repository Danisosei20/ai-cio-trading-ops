"""Research stages must fail closed when the TradingAgents runtime is missing."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import ta_interpreter
from scripts.desk_orchestrator import run_slot


class Proc:
    returncode = 0
    stdout = ""
    stderr = ""


class ResolverTests(unittest.TestCase):
    def test_default_is_sibling_venv(self):
        root = Path(r"C:\repo\desk")
        path = ta_interpreter.default_ta_python(root)
        self.assertIn("TradingAgents", path.parts)
        self.assertTrue(str(path).endswith("python.exe") or str(path).endswith("bin/python"))

    def test_env_override_absolute(self):
        with tempfile.TemporaryDirectory() as td:
            ta = Path(td) / "custom-python"
            with mock.patch.dict(os.environ, {"TRADINGAGENTS_VENV_PYTHON": str(ta)}):
                self.assertEqual(ta_interpreter.resolve_ta_python(Path(td)), ta)

    def test_env_override_relative_to_root(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"TRADINGAGENTS_VENV_PYTHON": "venv/python"}):
                self.assertEqual(ta_interpreter.resolve_ta_python(Path(td)),
                                 Path(td) / "venv" / "python")

    def test_missing_interpreter_fails_verification(self):
        with tempfile.TemporaryDirectory() as td:
            ok, detail = ta_interpreter.verify_ta_python(Path(td) / "nope")
            self.assertFalse(ok)
            self.assertIn("missing", detail)

    def test_failed_import_fails_verification(self):
        bad = mock.Mock(returncode=1, stdout="", stderr="ModuleNotFoundError: tradingagents")
        with mock.patch.object(ta_interpreter.subprocess, "run", return_value=bad):
            ok, detail = ta_interpreter.verify_ta_python(Path(__file__))
        self.assertFalse(ok)
        self.assertIn("cannot import tradingagents", detail)

    def test_require_raises_with_guidance(self):
        with mock.patch.object(ta_interpreter, "verify_ta_python",
                               return_value=(False, "missing: X")):
            with self.assertRaises(RuntimeError) as ctx:
                ta_interpreter.require_tradingagents_python(Path("root"))
        self.assertIn("TRADINGAGENTS_VENV_PYTHON", str(ctx.exception))
        self.assertIn("fail closed", str(ctx.exception))


class OrchestratorFailClosedTests(unittest.TestCase):
    def test_missing_ta_runtime_halts_and_never_falls_back(self):
        seen = []

        def fake(argv, **kw):
            seen.append(argv)
            return Proc()

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(ta_interpreter, "verify_ta_python",
                                   return_value=(False, "missing: X")):
                d = run_slot(root, "ta-missing", slot="0945", exec_cmd=fake,
                             now_iso="2026-11-23T09:45:00-05:00")

        self.assertEqual(d.verdict, "halt")
        reasons = " ".join(d.reasons)
        self.assertIn("TradingAgents runtime unavailable", reasons)
        research = [a for a in seen if any("paper_auto" in str(x) for x in a)]
        self.assertEqual(research, [], "no research stage may run without the LLM runtime")
        # Non-research stages still execute under the normal interpreter.
        self.assertTrue(seen)

    def test_explicit_mock_override_restores_mocked_launcher(self):
        seen = []

        def fake(argv, **kw):
            seen.append(argv)
            return Proc()

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = run_slot(root, "ta-mock", slot="0945", exec_cmd=fake,
                         _ta_python="fake-ta-python",
                         now_iso="2026-11-23T09:45:00-05:00")
        self.assertIn("halt", d.verdict)  # no seeded artifacts -> halt, not a runtime error
        research = [a for a in seen if any("paper_auto" in str(x) for x in a)]
        self.assertTrue(research)
        self.assertEqual(research[0][0], "fake-ta-python")


if __name__ == "__main__":
    unittest.main()
