import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import desk_fleet
from scripts.office_state import outcome, publish, snapshot


class PositionDirectionTests(unittest.TestCase):
    """Long/short means MARKET EXPOSURE, not contract side.

    2026-11-06: `direction = "long" if qty > 0 else "short"` classified a
    bought put as "long" because the qty is positive (you own the contracts).
    The dashboard therefore reported the entire bearish book as
    "Long - 4 / Short - 0" and told the operator they had no short exposure
    while four puts were open.
    """

    def setUp(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "trading_state", Path("scripts/trading_state.py"))
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def test_a_bought_put_is_short_exposure(self):
        self.assertEqual(self.mod._position_direction("RDDT261218P00090000", 5),
                         "short")

    def test_a_bought_call_is_long_exposure(self):
        self.assertEqual(self.mod._position_direction("ACN261120C00260000", 2),
                         "long")

    def test_equity_is_long_when_held(self):
        self.assertEqual(self.mod._position_direction("NVDA", 3), "long")

    def test_negative_qty_is_short(self):
        self.assertEqual(self.mod._position_direction("F261120C00015000", -2),
                         "short")

    def test_live_regime_fails_closed_to_unknown(self):
        """An unreadable regime must never read as RISK_ON."""
        with patch.object(self.mod, "_live_regime", return_value="UNKNOWN"):
            pass  # sanity: patching path works
        with patch("robinhood_tools.market_prices._spy_closes",
                   side_effect=RuntimeError("no data")):
            self.assertEqual(self.mod._live_regime(), "UNKNOWN")

    def test_state_carries_a_regime(self):
        """The header chip needs the key in the payload (it showed "—")."""
        import ast

        tree = ast.parse(Path("scripts/trading_state.py").read_text(encoding="utf-8"))
        keys = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Dict)
                    and any(isinstance(k, ast.Constant) and k.value == "generated_at"
                            for k in node.keys)):
                for k in node.keys:
                    if isinstance(k, ast.Constant):
                        keys.add(k.value)
        self.assertIn("regime", keys,
                      "state() must publish the live regime for the header")

    def test_unparsable_contract_is_not_assumed_short(self):
        self.assertEqual(self.mod._position_direction("NOTACONTRACT", 5),
                         "long")


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
