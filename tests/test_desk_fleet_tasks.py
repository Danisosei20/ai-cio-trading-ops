"""Tests for the desk fleet task pipeline (scripts/desk_fleet.py).

Covers the --tasks mode added for multi-task agents: arg parsing, the task
registry, unknown-task handling, the deterministic verify task, and the
report merge. Task mode is observe-only, so nothing here places orders.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import desk_fleet  # noqa: E402


class TestTaskModeParsing(unittest.TestCase):
    def test_tasks_flag_parses(self):
        a = desk_fleet.parse_args(["--tasks", "screen,verify"])
        self.assertEqual(a.tasks, "screen,verify")
        self.assertEqual(a.task_top, 3)
        self.assertEqual(a.cooldown, 5)
        self.assertFalse(a.no_notify)

    def test_agents_still_optional(self):
        a = desk_fleet.parse_args([])
        self.assertEqual(a.tasks, "")
        self.assertIsNone(a.agents)


class TestTaskRegistry(unittest.TestCase):
    def test_expected_tasks_present(self):
        for name in ("screen", "research", "news", "verify", "report"):
            self.assertIn(name, desk_fleet.TASKS)
            self.assertTrue(callable(desk_fleet.TASKS[name]))

    def test_unknown_task_returns_2(self):
        a = SimpleNamespace(tasks="bogus", cooldown=0, plan="")
        self.assertEqual(desk_fleet.run_tasks(a), 2)


class TestVerifyTask(unittest.TestCase):
    def test_shape_and_missing_tickers(self):
        ctx = {"tickers": [], "notes": []}
        res = desk_fleet.task_verify(ctx, SimpleNamespace(task_top=3))
        self.assertIn("ok", res)
        self.assertIn("checks", res)
        self.assertFalse(res["checks"]["has_tickers"])
        self.assertTrue(any("verify" in n for n in ctx["notes"]))

    def test_ok_true_with_tickers(self):
        ctx = {"tickers": ["AAA"], "notes": []}
        res = desk_fleet.task_verify(ctx, SimpleNamespace(task_top=3))
        # has_tickers now true; ok depends on screener freshness + venv only
        self.assertTrue(res["checks"]["has_tickers"])
        self.assertIsInstance(res["ok"], bool)


class TestReportTask(unittest.TestCase):
    def test_report_uses_planned_and_writes_summary(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "outputs").mkdir()
            with mock.patch.object(desk_fleet, "ROOT", Path(d)):
                ctx = {
                    "tickers": ["AAA", "BBB"],
                    "results": {"screen": {"count": 2}, "verify": {"ok": True}},
                    "notes": ["verify: one or more checks failed"],
                    "planned": ["screen", "verify", "report"],
                    "log_dir": Path(d) / "outputs" / "fleet" / "test",
                }
                (Path(d) / "outputs" / "fleet" / "test").mkdir(parents=True)
                res = desk_fleet.task_report(ctx, SimpleNamespace(no_notify=True))
                summary = Path(res["summary"])
                self.assertTrue(summary.exists())
                data = json.loads(summary.read_text())
                self.assertEqual(data["tasks"], ["screen", "verify", "report"])
                self.assertTrue(data["observe_only"])
                self.assertEqual(data["tickers"], ["AAA", "BBB"])
                self.assertEqual(len(data["notes"]), 1)


class TestResearchTaskNoTickers(unittest.TestCase):
    def test_research_without_screen_notes_and_noops(self):
        ctx = {"tickers": [], "notes": []}
        res = desk_fleet.task_research(ctx, SimpleNamespace(task_top=3, cooldown=0))
        self.assertEqual(res["tickers"], [])
        self.assertEqual(res["runs"], [])
        self.assertTrue(any("no tickers" in n for n in ctx["notes"]))


if __name__ == "__main__":
    unittest.main()
