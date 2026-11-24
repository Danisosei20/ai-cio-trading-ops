"""Phase 8.5: full-graph benchmarking, EOD verification and dashboard safety."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import eod_lifecycle_report as lifecycle
from scripts import signal_intelligence


class FullGraphBenchmarkTests(unittest.TestCase):
    def test_measurement_scope_label_honest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "outputs/shadow/research/fullgraph-NVDA-2026-11-24/measurement.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "scope": "full TradingAgents graph, single symbol, reduced config",
                "elapsed_seconds": 180.5, "decision": "Hold", "error": "",
                "shadow_only": True, "orders_submitted": 0}), encoding="utf-8")
            payload = signal_intelligence.signal_intelligence_payload(root, date="2026-11-24")
        full = payload["market_evidence"]["full_graph"]
        self.assertEqual(full["status"], "ok")
        self.assertEqual(full["elapsed_seconds"], 180.5)
        self.assertIn("full TradingAgents graph", full["scope"])
        self.assertEqual(payload["orders_submitted"], 0)

    def test_no_measurement_is_not_verified(self):
        with tempfile.TemporaryDirectory() as td:
            payload = signal_intelligence.signal_intelligence_payload(
                Path(td), date="2026-11-24")
        self.assertEqual(payload["market_evidence"]["full_graph"]["status"],
                         "not verified")

    def test_timed_out_measurement_is_not_verified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "outputs/shadow/research/fullgraph-NVDA-2026-11-24/measurement.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"error": "TIMEOUT after 420s",
                                        "elapsed_seconds": None,
                                        "scope": "full graph"}), encoding="utf-8")
            payload = signal_intelligence.signal_intelligence_payload(
                root, date="2026-11-24")
        self.assertEqual(payload["market_evidence"]["full_graph"]["status"],
                         "not verified")


class EodTests(unittest.TestCase):
    def test_powershell_date_and_content_check(self):
        self.assertTrue(lifecycle._dated_today("/Date(1795551000000)/", "2026-11-24"))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "outputs/options-watch.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("old", encoding="utf-8")
            tasks = {"AI-Desk-OptionsWatch-1510": {
                "Last": "/Date(1795551000000)/", "Result": 0}}
            with mock.patch.object(lifecycle, "_task_info", return_value=tasks), \
                 mock.patch.object(lifecycle, "_broker_state", return_value={}):
                report = lifecycle.build_lifecycle(
                    root, "2026-11-24", now="2026-11-24T17:00:00-05:00")
            self.assertEqual(report["events"][0]["status"], "INCOMPLETE")


class DashboardAndSecurityTests(unittest.TestCase):
    def test_dashboard_has_new_metrics(self):
        html = Path("web/agent-office/trading.html").read_text(encoding="utf-8")
        server = Path("scripts/agent_office.py").read_text(encoding="utf-8")
        self.assertIn("Immutable snapshots", html)
        self.assertIn("Full-graph elapsed (s)", html)
        self.assertNotIn("do_POST", server)

    def test_broker_values_not_fabricated(self):
        with tempfile.TemporaryDirectory() as td:
            payload = signal_intelligence.signal_intelligence_payload(
                Path(td), date="2026-11-24")
        broker = payload["broker_activity"]
        self.assertEqual(broker["submitted_paper_orders"], "not verified")
        self.assertEqual(broker["filled_paper_orders"], "not verified")

    def test_all_new_modules_avoid_order_paths(self):
        for rel in ("robinhood_tools/market_snapshot.py",
                    "robinhood_tools/signal_freshness.py",
                    "scripts/scout_factory_compare.py",
                    "scripts/signal_intelligence.py"):
            text = Path(rel).read_text(encoding="utf-8")
            for token in ("place_signal_order(", "place_long_option(",
                          "submit_order(", "authorize_entry_order("):
                self.assertNotIn(token, text, rel)


if __name__ == "__main__":
    unittest.main()
