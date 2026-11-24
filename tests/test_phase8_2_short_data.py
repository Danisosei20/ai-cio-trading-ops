"""Phase 8.2: short validation, market-data provenance, EOD and dashboard."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from robinhood_tools import market_data_evidence as mde
from scripts import daytrade_scout
from scripts import eod_lifecycle_report as lifecycle
from scripts import research_performance as rp
from scripts import setup_archive
from scripts import short_candidate_router as scr

DATE = "2026-11-24"


class ShortValidationTests(unittest.TestCase):
    def research(self):
        return scr.BearishResearch("AMD", "Underweight", "PUT", "RISK_OFF", "unit")

    def test_bearish_decision_is_not_an_automatic_short(self):
        self.assertFalse(self.research().automatic_short)

    def test_long_setup_is_rejected_before_short_validator(self):
        called = []
        result = scr.route_bearish_candidate(
            self.research(), setup={"setup": "long_reclaim", "price": 100},
            equity=100000,
            validate_setup=lambda *a: called.append("validate") or (True, "ok", {}),
            check_eligibility=lambda p: {"eligible": True},
            check_risk=lambda p: {"approved": True})
        self.assertEqual(called, [])
        self.assertTrue(any(r["code"] == "setup_direction_mismatch"
                            for r in result["rejections"]))

    def test_short_setup_borrow_evidence(self):
        result = scr.route_bearish_candidate(
            self.research(), setup={"setup": "short_breakdown", "price": 100},
            equity=100000,
            validate_setup=lambda s, e, p: (True, "ok", {
                "ticker": "AMD", "entry": 100, "stop": 105, "target": 90, "qty": 10}),
            check_eligibility=lambda p: {"eligible": False, "reason": "borrow unavailable"},
            check_risk=lambda p: {"approved": True})
        self.assertTrue(any(r["code"] == "short_eligibility_failed"
                            for r in result["rejections"]))

    def test_risk_check_receives_equity(self):
        seen = {}

        def risk(plan):
            seen.update(plan)
            return {"approved": True, "reason": "ok"}

        result = scr.route_bearish_candidate(
            self.research(), setup={"setup": "short_breakdown", "price": 100},
            equity=123456,
            validate_setup=lambda s, e, p: (True, "ok", {
                "ticker": "AMD", "entry": 100, "stop": 105, "target": 90, "qty": 10}),
            check_eligibility=lambda p: {"eligible": True},
            check_risk=risk)
        self.assertEqual(seen.get("equity"), 123456)
        self.assertEqual(result["decision_class"], "shadow_candidate")

    def test_vwap_and_opening_range_classification(self):
        self.assertEqual(daytrade_scout.classify_direction(95, 100, 94, 106), "short")
        self.assertEqual(daytrade_scout.classify_direction(105, 100, 94, 106), "long")
        self.assertEqual(daytrade_scout.classify_direction(100, 100, 94, 106), "range")

    def test_missing_bars_block_research(self):
        from robinhood_tools.research_metrics import deterministic_prefilter
        result = deterministic_prefilter(
            symbol="AMD", bars=3, min_bars=12, quote_age_seconds=5,
            max_quote_age_seconds=300, spread_pct=0.01, max_spread_pct=0.5,
            new_risk=True, regime="RISK_ON", session_open=True, cutoff_passed=False)
        self.assertFalse(result.eligible)
        self.assertEqual(result.gate, "history")


class MarketDataTests(unittest.TestCase):
    def test_iex_only_labeling(self):
        ev = mde.build_evidence(
            instrument="QQQ", source="alpaca", feed="iex", timeframe="5m",
            retrieved_at="2026-11-24T16:00:00+00:00",
            market_as_of="2026-11-24T15:55:00+00:00",
            now="2026-11-24T16:00:00+00:00")
        self.assertEqual(ev.volume_basis, "iex_only")
        self.assertIn("NOT consolidated", mde.describe_feed(ev))

    def test_stale_and_missing_as_of_block(self):
        ev = mde.build_evidence(
            instrument="QQQ", source="alpaca", feed="iex", timeframe="5m",
            retrieved_at="2026-11-24T16:00:00+00:00",
            market_as_of="2026-11-24T14:00:00+00:00",
            now="2026-11-24T16:00:00+00:00")
        self.assertEqual(ev.freshness, "stale")
        with self.assertRaises(mde.EvidenceIncomplete):
            mde.validate_evidence(ev)

    def test_cache_hit_still_requires_fresh_as_of(self):
        ev = mde.build_evidence(
            instrument="QQQ", source="alpaca", feed="iex", timeframe="5m",
            retrieved_at="2026-11-24T16:00:00+00:00",
            market_as_of="2026-11-24T15:59:00+00:00", cache_status="cache_hit",
            now="2026-11-24T16:00:00+00:00")
        self.assertEqual(mde.validate_evidence(
            ev, now="2026-11-24T16:00:00+00:00").cache_status, "cache_hit")


class SetupArchiveTests(unittest.TestCase):
    def test_point_in_time_setups_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "outputs/daytrade" / DATE / ".json"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(json.dumps({"generated_at": "x", "setups": [
                {"ticker": "QQQ", "setup": "short_breakdown"}]}), encoding="utf-8")
            dest = setup_archive.archive_setup(source.parent / f"{DATE}.json", root=root)
            self.assertIsNone(dest)
            (source.parent / f"{DATE}.json").write_text(source.read_text(encoding="utf-8"),
                                                        encoding="utf-8")
            dest = setup_archive.archive_setup(source.parent / f"{DATE}.json", root=root)
            self.assertIsNotNone(dest)
            rows = setup_archive.archived_records(root, DATE)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["setup"], "short_breakdown")


class EodAndDashboardTests(unittest.TestCase):
    def test_eod_status_vocabulary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tasks = {"AI-Desk-OptionsWatch-1510": {
                "Last": "11/24/2026 3:10:00 PM", "Result": 0},
                "AI-Desk-OvernightEnter": {"Last": "11/24/2026 3:40:00 PM", "Result": 1}}
            with mock.patch.object(lifecycle, "_task_info", return_value=tasks), \
                 mock.patch.object(lifecycle, "_broker_state", return_value={}):
                report = lifecycle.build_lifecycle(root, DATE, now="2026-11-24T17:00:00-05:00")
            statuses = {e["event"]: e["status"] for e in report["events"]}
            self.assertEqual(statuses["15:10 options watch"], "INCOMPLETE")
            self.assertEqual(statuses["15:40 overnight dry-run"], "FAILED")

    def test_research_performance_source_separation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "outputs/paper/tradingagents/live.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(
                f"{DATE}T10:45:16-05:00 [QQQ] researching (2026-11-24) via nvidia\n"
                f"{DATE}T10:52:39-05:00 [QQQ] decision: Sell\n",
                encoding="utf-8")
            shadow = root / "outputs/reports/shadow/2026-11-24-bearish-routing.json"
            shadow.parent.mkdir(parents=True, exist_ok=True)
            shadow.write_text(json.dumps({"candidates": [
                {"symbol": "QQQ", "rejections": [
                    {"gate": "setup", "code": "setup_direction_mismatch"}]}]}),
                encoding="utf-8")
            payload = rp.research_performance_payload(root, date=DATE)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["orders_submitted"], 0)
        self.assertEqual(payload["champion"]["completed"], 1)
        self.assertEqual(payload["short_diagnostics"]["data_quality_failures"], 1)
        self.assertIn("not submitted orders", payload["note"])

    def test_dashboard_route_and_section_present(self):
        html = Path("web/agent-office/trading.html").read_text(encoding="utf-8")
        src = Path("scripts/agent_office.py").read_text(encoding="utf-8")
        self.assertIn('id="research-performance"', html)
        self.assertIn("/api/research-performance", html)
        self.assertIn('route == "/api/research-performance"', src)
        self.assertNotIn("do_POST", src)

    def test_shadow_and_realized_pnl_are_separate(self):
        from scripts.risk_off_short_challenger import evaluate
        result = evaluate([{"date": "2026-11-01", "regime": "RISK_OFF",
                            "r_multiple": 2.0, "borrow_days": 1}])
        self.assertTrue(result["shadow_only"])
        self.assertEqual(result["orders_submitted"], 0)
        self.assertNotIn("realized_pnl", result)


class AdversarialDataTests(unittest.TestCase):
    def test_forged_freshness_label_cannot_bypass_validation(self):
        ev = mde.MarketDataEvidence(
            instrument="QQQ", source="alpaca", feed="iex", volume_basis="iex_only",
            timeframe="5m", retrieved_at="2026-01-01T16:00:00+00:00",
            market_as_of="2026-01-01T15:55:00+00:00", cache_status="fresh_fetch",
            freshness="fresh")
        with self.assertRaises(mde.EvidenceIncomplete):
            mde.validate_evidence(ev, now="2026-11-24T16:00:00+00:00")

    def test_running_task_code_is_incomplete_not_failed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tasks = {"AI-Desk-Flatten-1545": {
                "Last": "11/24/2026 3:45:00 PM", "Result": 267009}}
            with mock.patch.object(lifecycle, "_task_info", return_value=tasks), \
                 mock.patch.object(lifecycle, "_broker_state", return_value={}):
                report = lifecycle.build_lifecycle(root, DATE,
                                                   now="2026-11-24T17:00:00-05:00")
            self.assertTrue(any(e["status"] == "INCOMPLETE" for e in report["events"]))

    def test_challenger_batch_artifact_is_discovered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            batch = root / "outputs/shadow/research/r1/batch.json"
            batch.parent.mkdir(parents=True, exist_ok=True)
            batch.write_text(json.dumps({
                "mode": "laboratory", "completed": 3, "timeouts": 0,
                "batch_wall_seconds": 0.2,
                "outcomes": [{"symbol": "A", "queue_wait_seconds": 0.01,
                              "request_seconds": 0.05} for _ in range(3)]}),
                encoding="utf-8")
            payload = rp.research_performance_payload(root, date=DATE)
        self.assertEqual(payload["challenger"]["status"], "ok")
        self.assertEqual(payload["challenger"]["mode"], "laboratory")
        self.assertIn("LABORATORY", payload["challenger"]["measurement"])

    def test_shadow_record_is_never_marked_approved(self):
        result = scr.route_bearish_candidate(
            scr.BearishResearch("AMD", "Sell", "PUT", "RISK_OFF", "unit"))
        self.assertFalse(result["approved"])


if __name__ == "__main__":
    unittest.main()
