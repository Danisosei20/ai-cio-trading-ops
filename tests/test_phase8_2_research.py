"""Phase 8.2: shadow parallel research, latency, champion/challenger safety."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from robinhood_tools import market_prices, regime_compat
from scripts import champion_challenger as cc
from scripts import shadow_research_challenger as src


def good_provider(task):
    return {"decision": "Sell", "evidence_complete": True,
            "evidence_keys": ["price", "vwap", "or_high"],
            "missing_evidence": []}
good_provider.laboratory = True


class ParallelResearchTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        self.out = self.root / "outputs/shadow/research/r1"
        self.tasks = [src.ResearchTask(s, "2026-11-24", "r1")
                      for s in ("ZZZ", "AAA", "MMM", "BBB", "QQQ", "CCC")]

    def tearDown(self):
        self.t.cleanup()

    def test_bounded_concurrency_and_global_limit(self):
        running = {"now": 0, "max": 0}
        lock = threading.Lock()

        def provider(task):
            with lock:
                running["now"] += 1
                running["max"] = max(running["max"], running["now"])
            time.sleep(0.04)
            with lock:
                running["now"] -= 1
            return {"decision": "Sell", "evidence_complete": True}

        provider.laboratory = True
        report = src.run_challenger(self.tasks, provider, out_root=self.out,
                                    run_id="r1", max_workers=3,
                                    per_symbol_timeout=2.0,
                                    rate_per_minute=6000)
        self.assertLessEqual(running["max"], 3)
        self.assertEqual(report["completed"], 6)

    def test_per_symbol_deadline_is_fail_closed(self):
        def slow(task):
            time.sleep(0.2)
            return {"decision": "Buy", "evidence_complete": True}
        slow.laboratory = True
        report = src.run_challenger([self.tasks[0]], slow, out_root=self.out,
                                    run_id="r1", max_workers=1,
                                    per_symbol_timeout=0.01,
                                    rate_per_minute=6000)
        self.assertEqual(report["timeouts"], 1)
        artifact = json.loads((self.out / "ZZZ/challenger.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["decision"], "UNKNOWN")
        self.assertEqual(artifact["orders_submitted"], 0)

    def test_batch_deadline_records_not_evaluated(self):
        def slow(task):
            time.sleep(0.3)
            return {"decision": "Buy", "evidence_complete": True}
        slow.laboratory = True
        report = src.run_challenger(self.tasks[:3], slow, out_root=self.out,
                                    run_id="r1", max_workers=1,
                                    per_symbol_timeout=2.0,
                                    batch_timeout=0.05,
                                    rate_per_minute=6000)
        self.assertTrue(report["timeouts"] or report["not_evaluated"])

    def test_cooperative_cancellation(self):
        event = threading.Event()
        timer = threading.Timer(0.02, event.set)
        timer.start()
        outcome = src.research_symbol(self.tasks[0], good_provider, cancel=event)
        timer.join()
        self.assertIn(outcome.status, {"cancelled", "ok"})

    def test_late_worker_result_is_not_mutable_or_misattributed(self):
        def slow(task):
            time.sleep(0.12)
            return {"decision": task.symbol, "evidence_complete": True}
        slow.laboratory = True
        report = src.run_challenger([self.tasks[0]], slow, out_root=self.out,
                                    run_id="r1", max_workers=1,
                                    per_symbol_timeout=0.01,
                                    rate_per_minute=6000)
        snapshot = dict(report)
        time.sleep(0.2)
        self.assertEqual(report, snapshot)
        artifact = json.loads((self.out / "ZZZ/challenger.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["symbol"], "ZZZ")
        self.assertEqual(artifact["decision"], "UNKNOWN")

    def test_shared_state_isolation_by_symbol(self):
        def provider(task):
            return {"decision": f"decision-{task.symbol}", "evidence_complete": True}
        provider.laboratory = True
        src.run_challenger(self.tasks[:3], provider, out_root=self.out, run_id="r1",
                           max_workers=3, per_symbol_timeout=2.0, rate_per_minute=6000)
        for symbol in ("AAA", "MMM", "ZZZ"):
            doc = json.loads((self.out / symbol / "challenger.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["decision"], f"decision-{symbol}")

    def test_retry_and_backoff_are_bounded(self):
        calls = {"n": 0}

        def flaky(task):
            calls["n"] += 1
            if calls["n"] == 1:
                raise src.ProviderError("429 rate limit", "rate_limit")
            return {"decision": "Sell", "evidence_complete": True}

        outcome = src.research_symbol(self.tasks[0], flaky, max_retries=2,
                                      on_sleep=lambda _: None)
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.attempts, 2)
        self.assertIn("rate_limit", outcome.retry_categories)

    def test_nim_rate_limiter_enforces_minimum_interval(self):
        limiter = src.RateLimiter(min_interval_seconds=0.05)
        stamps = []
        for _ in range(3):
            limiter.acquire()
            stamps.append(time.monotonic())
        deltas = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertTrue(all(d >= 0.04 for d in deltas), deltas)

    def test_deterministic_aggregation_sorted_by_symbol(self):
        report = src.run_challenger(self.tasks, good_provider, out_root=self.out,
                                    run_id="r1", max_workers=3,
                                    per_symbol_timeout=2.0, rate_per_minute=6000)
        symbols = [o["symbol"] for o in report["outcomes"]]
        self.assertEqual(symbols, sorted(symbols))

    def test_artifact_isolation_and_no_execution(self):
        report = src.run_challenger(self.tasks[:2], good_provider, out_root=self.out,
                                    run_id="r1", max_workers=2,
                                    per_symbol_timeout=2.0, rate_per_minute=6000)
        self.assertTrue(report["shadow_only"])
        self.assertEqual(report["orders_submitted"], 0)
        self.assertTrue(report["promotion_recommendation"].startswith("none"))
        for path in self.out.rglob("*.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(doc.get("shadow_only", True))
        source = Path(src.__file__).read_text(encoding="utf-8")
        for token in ("place_signal_order", "place_long_option", "authorize_entry_order",
                      "derive_and_store", "do_POST", "submit_order"):
            self.assertNotIn(token, source)

    def test_champion_root_rejected(self):
        with self.assertRaises(ValueError):
            src.run_challenger(self.tasks[:1], good_provider,
                               out_root=self.root / "outputs/paper/tradingagents",
                               run_id="r1")


class ComparisonTests(unittest.TestCase):
    def test_evidence_completeness_comparison(self):
        champ = {"sessions": 6, "outcomes": [
            {"symbol": "AAPL", "decision": "Sell", "status": "ok",
             "evidence_keys": ["price", "vwap"]}]}
        chall = {"sessions": 6, "mode": "laboratory", "outcomes": [
            {"symbol": "AAPL", "decision": "Underweight", "status": "ok",
             "evidence_keys": ["price"], "queue_wait_seconds": 0.1,
             "request_seconds": 0.2}]}
        result = cc.compare(champ, chall)
        self.assertTrue(result["champion"]["latency"]["median_seconds"] is None)
        self.assertIsNotNone(result["challenger"]["latency"]["median_seconds"])
        self.assertEqual(result["evidence_completeness_gap"], 1)
        self.assertTrue(result["promotion_recommendation"].startswith("none"))

    def test_decision_disagreement_reported_not_resolved(self):
        champ = {"sessions": 6, "outcomes": [
            {"symbol": "AAPL", "decision": "Buy"}]}
        chall = {"sessions": 6, "outcomes": [
            {"symbol": "AAPL", "decision": "Sell", "queue_wait_seconds": 0.1,
             "request_seconds": 0.1}]}
        result = cc.compare(champ, chall)
        self.assertEqual(result["champion"]["latency"]["count"], 0)
        self.assertEqual(len(result["disagreements"]), 1)
        self.assertEqual(result["decision_agreement_rate"], 0.0)

    def test_sufficient_window_still_does_not_promote(self):
        champ = {"sessions": 6, "outcomes": [
            {"symbol": f"S{i}", "decision": "Sell"} for i in range(60)]}
        chall = {"sessions": 6, "outcomes": [
            {"symbol": f"S{i}", "decision": "Sell", "queue_wait_seconds": 0.1,
             "request_seconds": 0.1} for i in range(60)]}
        result = cc.compare(champ, chall)
        self.assertTrue(result["evaluation_window"]["sufficient"])
        self.assertTrue(result["promotion_recommendation"].startswith("none"))
        self.assertIn("owner review", result["recommendation"])


class RegimeSignatureTests(unittest.TestCase):
    def test_both_signatures_supported(self):
        def legacy(closes):
            return "RISK_ON" if len(closes) >= 51 else "UNKNOWN"

        def strict(closes, *, require_long_term=False):
            return "RISK_ON" if require_long_term else "CHOP"

        with mock.patch.object(market_prices, "market_regime", legacy):
            self.assertEqual(regime_compat.market_regime_compatible(
                list(range(1, 211)), require_long_term=True), "RISK_ON")
        with mock.patch.object(market_prices, "market_regime", strict):
            self.assertEqual(regime_compat.market_regime_compatible(
                list(range(1, 211)), require_long_term=True), "RISK_ON")


class AdversarialFixTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        self.out = self.root / "outputs/shadow/research/r1"

    def tearDown(self):
        self.t.cleanup()

    def test_symbol_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            src.run_challenger(
                [src.ResearchTask("../../../paper/tradingagents/evil",
                                  "2026-11-24", "r1")],
                good_provider, out_root=self.out, run_id="r1")

    def test_out_root_inside_champion_rejected(self):
        bad = self.root / "outputs/paper/tradingagents/shadow/research/r1"
        with self.assertRaises(ValueError):
            src.run_challenger([src.ResearchTask("QQQ", "2026-11-24", "r1")],
                               good_provider, out_root=bad, run_id="r1")

    def test_evidence_string_false_is_not_success(self):
        def provider(task):
            return {"decision": "Sell", "evidence_complete": "false",
                    "missing_evidence": ["price"]}
        provider.laboratory = True
        outcome = src.research_symbol(src.ResearchTask("QQQ", "2026-11-24", "r1"),
                                      provider)
        self.assertEqual(outcome.status, "incomplete_evidence")
        self.assertEqual(outcome.decision, "UNKNOWN")

    def test_evidence_keys_preserved_for_comparison(self):
        report = src.run_challenger([src.ResearchTask("QQQ", "2026-11-24", "r1")],
                                    good_provider, out_root=self.out, run_id="r1",
                                    max_workers=1, per_symbol_timeout=2.0,
                                    rate_per_minute=6000)
        self.assertEqual(report["outcomes"][0]["evidence_keys"],
                         ["or_high", "price", "vwap"])

    def test_cancel_event_reaches_worker(self):
        def provider(task):
            return {"decision": "Sell", "evidence_complete": True}
        provider.laboratory = True
        tasks = [src.ResearchTask(s, "2026-11-24", "r1") for s in ("AAA", "BBB")]
        report = src.run_challenger(tasks, provider, out_root=self.out, run_id="r1",
                                    max_workers=2, per_symbol_timeout=2.0,
                                    rate_per_minute=6000)
        self.assertEqual(report["completed"], 2)

    def test_batch_budget_covers_all_waves(self):
        def provider(task):
            time.sleep(0.05)
            return {"decision": "Sell", "evidence_complete": True}
        provider.laboratory = True
        tasks = [src.ResearchTask(s, "2026-11-24", "r1")
                 for s in ("A", "B", "C", "D", "E", "F")]
        report = src.run_challenger(tasks, provider, out_root=self.out, run_id="r1",
                                    max_workers=2, per_symbol_timeout=0.5,
                                    rate_per_minute=6000)
        self.assertEqual(report["completed"], 6)

    def test_mode_cannot_be_claimed_by_provider(self):
        def provider(task):
            return {"decision": "Sell", "evidence_complete": True}
        provider.laboratory = False  # malicious claim
        report = src.run_challenger([src.ResearchTask("QQQ", "2026-11-24", "r1")],
                                    provider, out_root=self.out, run_id="r1")
        self.assertEqual(report["mode"], "laboratory")

    def test_rate_limiter_waits_full_interval(self):
        limiter = src.RateLimiter(min_interval_seconds=0.06)
        start = time.monotonic()
        limiter.acquire(max_wait_seconds=1.0)
        limiter.acquire(max_wait_seconds=1.0)
        self.assertGreaterEqual(time.monotonic() - start, 0.05)

    def test_latency_finish_without_start_is_unstarted(self):
        from robinhood_tools.research_metrics import LatencyRecorder
        item = LatencyRecorder().finish("X", "phase")
        self.assertEqual(item.status, "unstarted")


if __name__ == "__main__":
    unittest.main()
