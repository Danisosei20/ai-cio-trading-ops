"""Phase 8.6: graph harness, overnight fixes, lifecycle and dashboard."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import eod_lifecycle_report as lifecycle
from scripts import full_graph_harness as graph
from scripts import operational_reliability


class GraphHarnessTests(unittest.TestCase):
    def test_timeout_writes_terminal_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(graph.subprocess, "run",
                                   side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
                report = graph.measure(
                    ta_python=Path("python"), ta_root=Path("ta"),
                    run_id="timeout-test", root=root, symbol="NVDA", date="2026-11-24",
                    deadline=1.0, workers=1, tool_rounds=1, analysts="market")
            path = root / "outputs/shadow/research/timeout-test/measurement.json"
            self.assertTrue(path.exists())
            self.assertEqual(report["status"], "timeout")
            self.assertEqual(report["orders_submitted"], 0)

    def test_champion_path_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                graph._guard(Path(td) / "outputs/paper/tradingagents", "run1")

    def test_graph_isolation_and_no_order_imports(self):
        source = Path(graph.__file__).read_text(encoding="utf-8")
        for token in ("place_signal_order(", "submit_order(", "authorize_entry_order("):
            self.assertNotIn(token, source)
        self.assertIn("no broker", source.lower())


class OvernightTests(unittest.TestCase):
    def test_dry_run_wrapper_hardcoded(self):
        bat = Path("scripts/overnight_enter.bat").read_text(encoding="utf-8")
        self.assertIn("--dry-run", bat)
        plist = Path("deploy/com.openai.ai-cio-overnight-enter.plist.example")
        if plist.exists():
            self.assertIn("--dry-run", plist.read_text(encoding="utf-8"))

    def test_unguarded_spy_fetch_replaced(self):
        source = Path("scripts/overnight_pilot.py").read_text(encoding="utf-8")
        self.assertIn("_spy_closes()", source)
        self.assertNotIn('yf.Ticker("SPY").history', source)

    def test_unsupported_setup_is_explicit(self):
        source = Path("scripts/overnight_pilot.py").read_text(encoding="utf-8")
        self.assertIn("unsupported overnight setup", source)


class LifecycleAndDashboardTests(unittest.TestCase):
    def test_manifest_failed_beats_scheduler_zero(self):
        from robinhood_tools.workflow_manifest import write_manifest
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_manifest(root, task="flatten", date="2026-11-24",
                           status="FAILED", run_id="r1", started_at="s",
                           finished_at="f", exit_code=1, mode="paper")
            log = root / "outputs/flatten.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("2026-11-24 flatten", encoding="utf-8")
            tasks = {"AI-Desk-Flatten-1545": {
                "Last": "/Date(1795553100000)/", "Result": 0}}
            with mock.patch.object(lifecycle, "_task_info", return_value=tasks), \
                 mock.patch.object(lifecycle, "_broker_state", return_value={}):
                report = lifecycle.build_lifecycle(
                    root, "2026-11-24", now="2026-11-24T17:00:00-05:00")
            self.assertEqual(report["events"][2]["status"], "FAILED")

    def test_dashboard_payload(self):
        from robinhood_tools.workflow_manifest import write_manifest
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_manifest(root, task="flatten-verify", date="2026-11-24",
                           status="NO_ACTION_REQUIRED", run_id="r1",
                           started_at="s", finished_at="f", exit_code=0,
                           mode="paper_read_only")
            payload = operational_reliability.operational_reliability_payload(
                root, date="2026-11-24")
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["workflows"][2]["status"], "NO_ACTION_REQUIRED")
            self.assertEqual(payload["orders_submitted"], 0)

    def test_dashboard_read_only(self):
        html = Path("web/agent-office/trading.html").read_text(encoding="utf-8")
        server = Path("scripts/agent_office.py").read_text(encoding="utf-8")
        self.assertIn('id="operational-reliability"', html)
        self.assertIn("/api/operational-reliability", server)
        self.assertNotIn("do_POST", server)


class DailyRehearsalTests(unittest.TestCase):
    def test_nominal_rehearsal(self):
        from scripts import daily_reliability_rehearsal as rehearsal
        with tempfile.TemporaryDirectory() as td:
            result = rehearsal.run(Path(td))
        self.assertTrue(result["passed"])
        self.assertEqual(result["failed_events"], [])
        self.assertEqual(result["orders_submitted"], 0)
        self.assertTrue(result["overnight_dry_run"])

    def test_injected_failure_is_propagated(self):
        from scripts import daily_reliability_rehearsal as rehearsal
        with tempfile.TemporaryDirectory() as td:
            result = rehearsal.run(Path(td), fail_stage="flatten-verify")
        self.assertTrue(result["passed"])
        self.assertEqual(result["failed_events"],
                         ["15:45 intraday flatten"])
        self.assertEqual(result["dashboard_last_failed"], ["flatten-verify"])
        self.assertEqual(result["orders_submitted"], 0)
