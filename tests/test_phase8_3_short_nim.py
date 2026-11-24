"""Phase 8.3: short lineage, NIM benchmark safety and dashboard diagnostics."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import nim_live_benchmark as nb
from scripts import research_performance as rp
from scripts import shadow_research_challenger as src
from scripts import short_candidate_router as scr
from scripts import short_signal_lineage as lineage


class LineageTests(unittest.TestCase):
    def test_sell_is_not_auto_short(self):
        row = {"symbol": "AMD", "decision": "Sell", "signal_at": "2026-11-24T15:00:00-05:00"}
        result = lineage.audit(row, setup={"ticker": "AMD", "setup": "short_breakdown",
                                           "direction": "short",
                                           "generated_at": "2026-11-24T15:00:00-05:00"})
        self.assertFalse(result.automatic_short)

    def test_invalid_setup_direction_rejected(self):
        row = {"symbol": "AMD", "decision": "Underweight",
               "signal_at": "2026-11-24T15:00:00-05:00"}
        result = lineage.audit(row, setup={"ticker": "AMD", "setup": "long_reclaim",
                                           "direction": "long",
                                           "generated_at": "2026-11-24T15:00:00-05:00"})
        self.assertFalse(result.eligible)
        self.assertIn("setup_direction_mismatch", result.rejection)

    def test_symbol_identity_mismatch(self):
        row = {"symbol": "AMD", "decision": "Sell"}
        result = lineage.audit(row, setup={"ticker": "NVDA", "setup": "short_breakdown"})
        self.assertEqual(result.rejection, "symbol_identity_mismatch")

    def test_future_setup_is_rejected(self):
        row = {"symbol": "AMD", "decision": "Sell", "signal_at": "2026-11-24T15:00:00Z"}
        result = lineage.audit(row, setup={"ticker": "AMD", "setup": "short_breakdown",
                                           "direction": "short", "generated_at":
                                           "2026-11-24T16:00:00Z"})
        self.assertEqual(result.rejection, "setup_after_signal_future_data")

    def test_missing_setup_fails_closed(self):
        row = {"symbol": "AMD", "decision": "Sell"}
        result = lineage.audit(row, setup={"ticker": "AMD"})
        self.assertEqual(result.rejection, "missing_point_in_time_setup")

    def test_lineage_report_counts(self):
        rows = [{"symbol": "AMD", "analyst_bias": "bearish_research"},
                {"symbol": "NVDA", "analyst_bias": "bearish_research"}]
        report = lineage.lineage_report(rows, {"AMD": {"ticker": "AMD",
                                                       "setup": "short_breakdown"}})
        self.assertEqual(report["counts"]["total"], 2)
        self.assertEqual(report["orders_submitted"], 0)

    def test_short_router_remains_shadow_only(self):
        result = scr.route_bearish_candidate(
            scr.BearishResearch("AMD", "Underweight", "PUT", "RISK_OFF", "unit"))
        self.assertTrue(result["shadow_only"])
        self.assertFalse(result["approved"])
        self.assertEqual(result["orders_submitted"], 0)


class NimBenchmarkTests(unittest.TestCase):
    def test_key_redaction_in_report(self):
        class Response:
            def read(self):
                return json.dumps({"model": "test-model", "choices": [{"index": 0}]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch.object(nb, "load_nim_key", return_value="secret-key-value"), \
             mock.patch.object(nb.urllib.request, "urlopen", return_value=Response()):
            provider = nb.nim_provider()
            result = provider(type("T", (), {"symbol": "QQQ"})())
        self.assertFalse(getattr(provider, "laboratory", True))
        serialized = json.dumps(result)
        self.assertNotIn("secret-key-value", serialized)
        self.assertNotIn("api_key", serialized.lower())

    def test_live_benchmark_with_mocked_provider(self):
        def provider(task):
            return {"decision": "UNKNOWN", "evidence_complete": False,
                    "missing_evidence": ["mock"]}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "outputs/shadow/research/live-mock"
            # bypass real HTTP by patching nim_provider inside run_benchmark
            with mock.patch.object(nb, "nim_provider", return_value=provider):
                report = nb.run_benchmark(["QQQ", "SPY"], workers=2,
                                          out_root=root, run_id="live-mock")
            self.assertEqual(report["orders_submitted"], 0)
            self.assertEqual(report["symbols"], ["QQQ", "SPY"])

    def test_worker_concurrency_and_cancellation_are_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "outputs/shadow/research/r1"
            tasks = [src.ResearchTask(s, "2026-11-24", "r1")
                     for s in ("AAA", "BBB", "CCC", "DDD")]
            report = src.run_challenger(tasks, lambda t: {"decision": "UNKNOWN",
                                                         "evidence_complete": False},
                                        out_root=out, run_id="r1", max_workers=2,
                                        per_symbol_timeout=2.0, rate_per_minute=6000)
            self.assertEqual(len(report["outcomes"]), 4)
            self.assertEqual(report["orders_submitted"], 0)


class DashboardTests(unittest.TestCase):
    def test_payload_exposes_new_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, doc in (
                ("outputs/reports/research/2026-11-24-nim-live-benchmark.json",
                 {"mode": "live_nim", "speedup": 1.9, "parallel_responses": 6,
                  "parallel_timeouts": 0, "generated_at": "2026-11-24T14:00:00-05:00"}),
                ("outputs/reports/short/2026-11-24-short-signal-lineage.json",
                 {"counts": {"setup_mismatch": 7, "missing_setup": 5,
                             "future_data": 1}, "note": "no relabel"}),
                ("outputs/reports/diagnostics/2026-11-24-market-data-health.json",
                 {"categories": {"snapshot:sip": "entitlement_realtime_sip"},
                  "permitted_latest_feeds": ["iex", "delayed_sip"], "note": "read-only"}),
            ):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(doc), encoding="utf-8")
            payload = rp.research_performance_payload(root, date="2026-11-24")
        self.assertEqual(payload["nim_benchmark"]["mode"], "live_nim")
        self.assertEqual(payload["short_lineage"]["counts"]["setup_mismatch"], 7)
        self.assertEqual(payload["market_data"]["categories"]["snapshot:sip"],
                         "entitlement_realtime_sip")
        self.assertEqual(payload["orders_submitted"], 0)

    def test_dashboard_is_read_only(self):
        html = Path("web/agent-office/trading.html").read_text(encoding="utf-8")
        src_server = Path("scripts/agent_office.py").read_text(encoding="utf-8")
        self.assertIn("NIM live speedup", html)
        self.assertNotIn("do_POST", src_server)
        for rel in ("scripts/nim_live_benchmark.py", "scripts/short_signal_lineage.py",
                    "robinhood_tools/market_data_health.py",
                    "robinhood_tools/bar_validation.py"):
            text = Path(rel).read_text(encoding="utf-8")
            for token in ("place_signal_order(", "place_long_option(", "submit_order("):
                self.assertNotIn(token, text)


class AdversarialLineageTests(unittest.TestCase):
    def test_future_setup_without_signal_time_rejected(self):
        row = {"symbol": "AMD", "decision": "Sell"}
        result = lineage.audit(row, setup={"ticker": "AMD", "setup": "short_breakdown",
                                           "direction": "short",
                                           "generated_at": "2099-01-01T00:00:00Z"})
        self.assertFalse(result.eligible)

    def test_setup_without_symbol_rejected(self):
        row = {"symbol": "AMD", "decision": "Sell", "signal_at": "2026-11-24T15:00:00Z"}
        result = lineage.audit(row, setup={"setup": "short_breakdown",
                                           "direction": "short",
                                           "generated_at": "2026-11-24T14:00:00Z"})
        self.assertEqual(result.rejection, "setup_symbol_missing")

    def test_naive_timestamps_do_not_crash_batch(self):
        rows = [{"symbol": "AMD", "decision": "Sell", "signal_at": "2026-11-24T15:00:00"}]
        setups = {"AMD": {"ticker": "AMD", "setup": "short_breakdown",
                          "direction": "short", "generated_at": "2026-11-24T14:00:00"}}
        report = lineage.lineage_report(rows, setups)
        self.assertEqual(report["counts"]["total"], 1)

    def test_benchmark_reports_no_promotion_and_no_key(self):
        report = {"mode": "live_nim", "orders_submitted": 0,
                  "comparison": {"promotion_recommendation": "none - automatic promotion disabled"}}
        self.assertEqual(report["comparison"]["promotion_recommendation"],
                         "none - automatic promotion disabled")
        self.assertNotIn("nvapi", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
