import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import desk_fleet
from scripts.office_state import outcome, publish, snapshot


class OfficeStateTests(unittest.TestCase):
    def test_failures_are_not_completed(self):
        self.assertEqual(outcome({"runs": [{"returncode": 1}]}), "blocked")
        self.assertEqual(outcome({"ok": False}), "blocked")
        self.assertEqual(outcome({"returncode": 1}), "blocked")
        self.assertEqual(outcome({"runs": []}), "skipped")
        self.assertEqual(outcome({"runs": [{"returncode": 0}]}), "complete")

    def test_live_finished_and_stale(self):
        with tempfile.TemporaryDirectory() as root:
            publish(root, "test", {"research": "working"})
            self.assertEqual(snapshot(root)["mode"], "live")
            path = Path(root) / "outputs/office-state.json"
            data = json.loads(path.read_text())
            data["updated"] = time.time() - 60
            path.write_text(json.dumps(data))
            self.assertEqual(snapshot(root)["tasks"]["research"], "unknown")
            publish(root, "test", {"research": "complete"}, finished=True)
            self.assertEqual(snapshot(root)["mode"], "idle")

    def test_pipeline_publishes_working_then_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            def research(ctx, args):
                self.assertEqual(snapshot(root)["tasks"]["research"], "working")
                return {"runs": [{"returncode": 1}]}
            with patch.object(desk_fleet, "ROOT", Path(root)), patch.dict(desk_fleet.TASKS, {"research": research}):
                args = desk_fleet.parse_args(["--tasks", "research", "--cooldown", "0", "--no-notify"])
                desk_fleet.run_tasks(args)
            self.assertEqual(snapshot(root)["tasks"]["research"], "blocked")
            self.assertEqual(snapshot(root)["mode"], "idle")

    def test_crashed_task_is_blocked_and_not_live(self):
        with tempfile.TemporaryDirectory() as root:
            def fail(ctx, args):
                raise RuntimeError("test failure")
            with patch.object(desk_fleet, "ROOT", Path(root)), patch.dict(desk_fleet.TASKS, {"research": fail}):
                with self.assertRaises(RuntimeError):
                    desk_fleet.run_tasks(desk_fleet.parse_args(["--tasks", "research"]))
            self.assertEqual(snapshot(root)["tasks"]["research"], "blocked")
            self.assertEqual(snapshot(root)["mode"], "idle")

    def test_missing_corrupt_and_historical(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(snapshot(root)["mode"], "idle")
            folder = Path(root) / "outputs"
            folder.mkdir()
            (folder / "office-state.json").write_text("{bad")
            self.assertEqual(snapshot(root)["tasks"]["news"], "idle")
            (folder / "fleet-summary.json").write_text(json.dumps({"results": {"news": {"runs": [{"returncode": 1}]}}}))
            self.assertEqual(snapshot(root)["tasks"]["news"], "blocked")
            self.assertEqual(snapshot(root)["mode"], "idle")
