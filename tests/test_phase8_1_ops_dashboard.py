"""Phase 8.1 operations and dashboard: lifecycle, data consistency, safety."""
import json
import os
import tempfile
from datetime import datetime
import unittest
from pathlib import Path
from unittest import mock

from scripts import eod_lifecycle_report as lifecycle
from scripts import intraday_pipeline
from scripts import opportunity_analysis


DATE = "2026-11-24"


def write_report(root: Path, *, complete=False) -> Path:
    path = root / "outputs/reports/daily" / f"{DATE}-trading-review.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "generated_at": "2026-11-24T12:00:00-05:00", "session_date": DATE,
        "as_of": "2026-11-24T12:00:00-05:00", "session_complete": complete,
        "entry_permission": "none - research and reporting only",
        "enforcement_enabled": False,
        "market_conditions": {"regimes_recorded": ["RISK_OFF"],
                              "vix_values_in_rejections": [27.4]},
        "trading_activity": {"symbols_scanned": ["QQQ", "SPY"], "cycles": 2,
                             "setups_recorded": 2, "judge_completed_batches": 1,
                             "judge_failures": 0,
                             "cycle_details": ["cycle 1", "cycle 2"]},
        "trade_decisions": {"long_bias": 1, "bearish_research": 1,
                            "approved_entries": 0, "valid_policy_rejections": 2,
                            "technical_failures": 0, "shadow_rows": 2,
                            "broker_orders": 0},
        "risk_management": {"vix_blocks": 1, "regime_blocks": 1,
                            "no_short_path": 0, "emergency_kill": "off"},
        "long_short_performance": {"realized_paper": {"filled_orders": 0}},
        "missed_opportunities": {"rows": [
            {"symbol": "QQQ", "analyst_bias": "bearish_research",
             "rejection_category": "no_short_entry_path",
             "final_classification": "valid_policy_rejection",
             "rejection_reason": "bearish (Sell) but no position held", "setup": None},
            {"symbol": "SPY", "analyst_bias": "long",
             "rejection_category": "regime_policy",
             "final_classification": "valid_policy_rejection",
             "rejection_reason": "regime RISK_OFF: new risk blocked desk-wide", "setup": None},
        ]},
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class LifecycleTests(unittest.TestCase):
    def test_missing_afternoon_artifacts_are_not_verified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(lifecycle, "_task_info", return_value={}), \
                 mock.patch.object(lifecycle, "_broker_state", return_value={"status": "not_verified"}):
                report = lifecycle.build_lifecycle(root, DATE, now="2026-11-24T12:00:00-05:00")
            self.assertFalse(report["session_complete"])
            self.assertTrue(all(e["verdict"] == "Not verified" for e in report["events"]))
            self.assertEqual(len(report["events"]), 5)
            self.assertEqual(report["orders_submitted"], 0)

    def test_session_completeness_uses_injected_clock(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(lifecycle, "_task_info", return_value={}), \
                 mock.patch.object(lifecycle, "_broker_state", return_value={}):
                report = lifecycle.build_lifecycle(
                    root, DATE, now="2026-11-24T17:00:00-05:00")
            self.assertTrue(report["session_complete"])

    def test_completed_event_requires_artifact_and_zero_result(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact = root / "outputs/options-watch.log"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(DATE + " options watch done", encoding="utf-8")
            stamp = datetime(2026, 11, 24, 15, 10).timestamp()
            os.utime(artifact, (stamp, stamp))
            tasks = {"AI-Desk-OptionsWatch-1510": {
                "Last": "11/24/2026 3:10:00 PM", "Result": 0}}
            with mock.patch.object(lifecycle, "_task_info", return_value=tasks), \
                 mock.patch.object(lifecycle, "_broker_state", return_value={}):
                report = lifecycle.build_lifecycle(root, DATE)
            first = report["events"][0]
            self.assertEqual(first["scheduled_state"], "Completed")
            self.assertEqual(first["verdict"], "Completed")

    def test_failed_task_result_is_failed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tasks = {"AI-Desk-OvernightEnter": {
                "Last": "11/24/2026 3:40:00 PM", "Result": 1}}
            with mock.patch.object(lifecycle, "_task_info", return_value=tasks), \
                 mock.patch.object(lifecycle, "_broker_state", return_value={}):
                report = lifecycle.build_lifecycle(root, DATE)
            self.assertEqual(report["events"][1]["scheduled_state"], "Failed")


class DashboardConsistencyTests(unittest.TestCase):
    def test_pipeline_payload_matches_review_counts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_report(root)
            payload = intraday_pipeline.intraday_pipeline_payload(root)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["risk_blocked"], 2)
            self.assertEqual(payload["symbols_screened"], 2)
            self.assertEqual(payload["actual_fills"], 0)
            self.assertEqual(payload["submitted_orders"], 0)
            self.assertEqual(len(payload["candidates"]), 2)
            self.assertIn("Screened", payload["candidates"][0]["path"])

    def test_pipeline_unavailable_on_empty_root(self):
        with tempfile.TemporaryDirectory() as td:
            payload = intraday_pipeline.intraday_pipeline_payload(Path(td))
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["orders_submitted"], 0)

    def test_opportunity_and_pipeline_share_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_report(root)
            a = opportunity_analysis.opportunity_analysis_payload(root)
            b = intraday_pipeline.intraday_pipeline_payload(root)
            self.assertEqual(a["source"], b["source"])
            self.assertEqual(a["orders_submitted"], b["orders_submitted"])

    def test_html_and_route_are_present_and_read_only(self):
        html = Path("web/agent-office/trading.html").read_text(encoding="utf-8")
        src = Path("scripts/agent_office.py").read_text(encoding="utf-8")
        self.assertIn('id="intraday-decision-pipeline"', html)
        self.assertIn("/api/intraday-decision-pipeline", html)
        self.assertIn('route == "/api/intraday-decision-pipeline"', src)
        self.assertNotIn("do_POST", src)
        self.assertNotIn("current_positions', d.positions", html)


class NoOrderSubmissionTests(unittest.TestCase):
    def test_analysis_modules_never_place_orders(self):
        for rel in ("scripts/short_candidate_router.py",
                    "scripts/risk_off_short_challenger.py",
                    "scripts/eod_lifecycle_report.py",
                    "scripts/intraday_pipeline.py",
                    "robinhood_tools/research_metrics.py",
                    "robinhood_tools/vix_evidence.py",
                    "robinhood_tools/regime_compat.py"):
            text = Path(rel).read_text(encoding="utf-8")
            for token in ("place_signal_order(", "place_long_option(",
                          "derive_and_store(", "submit_order(", "do_POST"):
                self.assertNotIn(token, text, rel)


if __name__ == "__main__":
    unittest.main()
