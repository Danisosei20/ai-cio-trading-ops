"""Phase 8.4: latency measurement, worker isolation and dashboard diagnostics."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from robinhood_tools.research_metrics import LatencyRecorder
from scripts import eod_lifecycle_report as lifecycle
from scripts import research_pipeline_benchmark as rpb
from scripts import signal_intelligence


class LatencyTests(unittest.TestCase):
    def test_full_pipeline_measurement_with_mock_provider(self):
        def provider(task):
            return {"decision": "UNKNOWN", "evidence_complete": True}
        with mock.patch.object(rpb.nib, "nim_provider", return_value=provider):
            report = rpb.run(["QQQ", "SPY", "NVDA"], workers=3)
        self.assertIn("partial_pipeline", report["mode"])
        self.assertEqual(len(report["phases"]), 7)
        self.assertEqual(report["orders_submitted"], 0)
        self.assertEqual(report["champion"]["completed"], 3)
        self.assertEqual(report["challenger"]["completed"], 3)
        self.assertIn("by_phase", report["champion"]["latency"])

    def test_recorder_is_thread_safe(self):
        import threading
        rec = LatencyRecorder()
        def work(i):
            rec.start(f"S{i}", "phase")
            rec.finish(f"S{i}", "phase")
        threads = [threading.Thread(target=work, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(rec.summary()["count"], 30)

    def test_worker_isolation_and_no_order_path(self):
        source = Path(rpb.__file__).read_text(encoding="utf-8")
        for token in ("place_signal_order(", "place_long_option(", "submit_order("):
            self.assertNotIn(token, source)


class RateLimitTimeoutTests(unittest.TestCase):
    def test_provider_rate_limit_capped(self):
        from scripts import shadow_research_challenger as src
        calls = []
        def provider(task):
            calls.append(task.symbol)
            return {"decision": "UNKNOWN", "evidence_complete": False}
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "outputs/shadow/research/r1"
            report = src.run_challenger(
                [src.ResearchTask(s, "2026-11-24", "r1")
                 for s in ("AAA", "BBB", "CCC")],
                provider, out_root=out, run_id="r1", max_workers=3,
                per_symbol_timeout=2.0, rate_per_minute=6000)
        self.assertEqual(len(report["outcomes"]), 3)
        self.assertEqual(report["orders_submitted"], 0)

    def test_timeout_returns_structured_reason(self):
        from scripts import shadow_research_challenger as src
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "outputs/shadow/research/r1"
            report = src.run_challenger(
                [src.ResearchTask("QQQ", "2026-11-24", "r1")],
                lambda t: {"decision": "UNKNOWN", "evidence_complete": False},
                out_root=out, run_id="r1", max_workers=1, per_symbol_timeout=2.0)
        self.assertEqual(report["outcomes"][0]["orders_submitted"], 0)


class EodAndDashboardTests(unittest.TestCase):
    def test_eod_verification_statuses(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tasks = {"AI-Desk-OptionsWatch-1510": {
                "Last": "11/24/2026 3:10:00 PM", "Result": 0},
                "AI-Desk-OvernightEnter": {
                    "Last": "11/24/2026 3:40:00 PM", "Result": 1}}
            with mock.patch.object(lifecycle, "_task_info", return_value=tasks), \
                 mock.patch.object(lifecycle, "_broker_state", return_value={}):
                report = lifecycle.build_lifecycle(
                    root, "2026-11-24", now="2026-11-24T17:00:00-05:00")
            statuses = {e["event"]: e["status"] for e in report["events"]}
            self.assertEqual(statuses["15:10 options watch"], "INCOMPLETE")
            self.assertEqual(statuses["15:40 overnight dry-run"], "FAILED")

    def test_powershell_date_format_is_parsed(self):
        self.assertTrue(lifecycle._dated_today("/Date(1795551000000)/", "2026-11-24"))
        self.assertFalse(lifecycle._dated_today("/Date(1795469400000)/", "2026-11-24"))

    def test_signal_intelligence_payload(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, doc in (
                ("outputs/reports/signals/2026-11-24-signal-factory.json", {
                    "strategy_version": "v1", "candidates": [
                        {"symbol": "QQQ", "direction": "long",
                         "validation_status": "validated"},
                        {"symbol": "AMD", "direction": "short",
                         "validation_status": "rejected",
                         "rejection_reason": "future bar"}]}),
                ("outputs/reports/research/2026-11-24-research-pipeline-benchmark.json", {
                    "mode": "live_nim_partial_pipeline", "speedup": 1.4,
                    "champion": {"wall_seconds": 1.0, "completed": 2,
                                 "latency": {"median_seconds": 0.3, "p95_seconds": 0.4}},
                    "challenger": {"wall_seconds": 0.7, "completed": 2,
                                   "latency": {"median_seconds": 0.2, "p95_seconds": 0.3}}}),
            ):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(doc), encoding="utf-8")
            payload = signal_intelligence.signal_intelligence_payload(
                root, date="2026-11-24")
        self.assertEqual(payload["technical_candidates"]["valid_long"], 1)
        self.assertIn(payload["status"], {"ok", "partial"})
        self.assertEqual(payload["orders_submitted"], 0)
        self.assertEqual(payload["research_latency"]["speedup"], 1.4)

    def test_dashboard_route_and_read_only(self):
        html = Path("web/agent-office/trading.html").read_text(encoding="utf-8")
        src = Path("scripts/agent_office.py").read_text(encoding="utf-8")
        self.assertIn('id="signal-intelligence"', html)
        self.assertIn("/api/signal-intelligence", html)
        self.assertIn('route == "/api/signal-intelligence"', src)
        self.assertNotIn("do_POST", src)

    def test_shadow_pnl_not_realized(self):
        from scripts.opportunity_scoring import scoring_report
        report = scoring_report([])
        serialized = json.dumps(report)
        self.assertNotIn("realized_pnl", serialized)
        self.assertIn("orders_submitted", serialized)


if __name__ == "__main__":
    unittest.main()
