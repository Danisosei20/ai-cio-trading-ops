"""Phase 8.1 engineering: regime compatibility, latency, shadow routing, VIX."""
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from robinhood_tools import market_prices, option_entry_risk, regime_compat
from robinhood_tools import research_metrics as rm
from robinhood_tools import vix_evidence as ve
from scripts import short_candidate_router as scr


def legacy_regime(closes):
    if len(closes) < 51:
        return "UNKNOWN"
    mean = sum(closes[-51:-1]) / 50
    return "RISK_ON" if closes[-1] > mean else "CHOP" if closes[-1] == mean else "RISK_OFF"


def strict_regime(closes, *, require_long_term=False):
    if len(closes) < 51 or (require_long_term and len(closes) < 201):
        return "UNKNOWN"
    mean = sum(closes[-51:-1]) / 50
    base = "RISK_ON" if closes[-1] > mean else "CHOP" if closes[-1] == mean else "RISK_OFF"
    if not require_long_term or base != "RISK_ON":
        return base
    long_mean = sum(closes[-201:-1]) / 200
    return ("RISK_ON" if closes[-1] > long_mean else
            "CHOP" if closes[-1] == long_mean else "RISK_OFF")


class RegimeCompatibilityTests(unittest.TestCase):
    def test_signature_compatibility_with_legacy_and_strict(self):
        closes = list(range(100, 301))
        with mock.patch.object(market_prices, "market_regime", legacy_regime):
            self.assertFalse(regime_compat.supports_long_term())
            self.assertEqual(regime_compat.market_regime_compatible(
                closes, require_long_term=False), "RISK_ON")
            self.assertEqual(regime_compat.market_regime_compatible(
                closes, require_long_term=True), "RISK_ON")
        with mock.patch.object(market_prices, "market_regime", strict_regime):
            self.assertTrue(regime_compat.supports_long_term())
            self.assertEqual(regime_compat.market_regime_compatible(
                closes, require_long_term=True), "RISK_ON")

    def test_require_long_term_downgrades_only_bullish_base(self):
        closes = [220.0] * 150 + [100.0] * 50 + [150.0]
        with mock.patch.object(market_prices, "market_regime", legacy_regime):
            self.assertEqual(regime_compat.market_regime_compatible(closes), "RISK_ON")
            self.assertEqual(regime_compat.market_regime_compatible(
                closes, require_long_term=True), "RISK_OFF")

    def test_clean_checkout_caller_has_no_keyword_error(self):
        src = Path("scripts/tradingagents_paper_auto.py").read_text(encoding="utf-8")
        self.assertIn("from robinhood_tools.regime_compat import regime_for_options", src)
        self.assertNotIn("require_long_term=options_mode)\n", src)

    def test_stale_and_short_evidence_fail_closed(self):
        with mock.patch.object(market_prices, "market_regime", legacy_regime):
            self.assertEqual(regime_compat.market_regime_compatible(
                [1.0] * 10, require_long_term=True), "UNKNOWN")
            self.assertEqual(regime_compat.market_regime_compatible(
                [1.0] * 100, require_long_term=True), "UNKNOWN")


class LatencyTests(unittest.TestCase):
    def test_recorder_uses_monotonic_and_utc(self):
        rec = rm.LatencyRecorder(clock=lambda: 5.0, now=lambda: "2026-01-01T00:00:00+00:00")
        rec.start("AAPL", "research")
        item = rec.finish("AAPL", "research")
        self.assertEqual(item.duration_seconds, 0.0)
        self.assertIn("T", item.started_at)
        self.assertEqual(rec.summary()["count"], 1)

    def test_summary_median_and_p95(self):
        rec = rm.LatencyRecorder()
        for value in (1.0, 2.0, 3.0, 10.0):
            rec.record("X", "phase", duration_seconds=value)
        summary = rec.summary()
        self.assertEqual(summary["median_seconds"], 3.0)
        self.assertEqual(summary["p95_seconds"], 10.0)

    def test_bounded_concurrency_limit(self):
        active = {"now": 0, "max": 0}
        lock = threading.Lock()

        def work(_):
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.02)
            with lock:
                active["now"] -= 1
            return "ok"

        result = rm.bounded_map(list(range(6)), work, max_workers=2,
                                per_item_timeout=1.0)
        self.assertLessEqual(active["max"], 2)
        self.assertEqual(len(result.results), 6)
        self.assertEqual(result.timed_out, [])

    def test_per_item_timeout_records_unfinished(self):
        def slow(_):
            time.sleep(0.3)
            return "slow"

        result = rm.bounded_map([0], slow, max_workers=1,
                                per_item_timeout=0.01)
        self.assertEqual(result.timed_out, [0])
        self.assertEqual(result.results, {})

    def test_caller_cancel_event_is_not_mutated(self):
        event = threading.Event()
        result = rm.bounded_map([1, 2, 3], lambda x: x, max_workers=2,
                                per_item_timeout=1.0, cancel=event)
        self.assertFalse(event.is_set())
        self.assertEqual(len(result.results), 3)

    def test_cooperative_cancellation_stops_work(self):
        event = threading.Event()

        def work(item, cancel):
            while not cancel.is_set():
                time.sleep(0.005)
            return "cancelled"

        timer = threading.Timer(0.03, event.set)
        timer.start()
        result = rm.bounded_map([1], work, max_workers=1,
                                per_item_timeout=1.0, cancel=event)
        timer.join()
        self.assertTrue(event.is_set())
        self.assertTrue(result.deadline_exceeded or result.results)

    def test_result_object_is_immutable_after_timeout(self):
        def slow(_):
            time.sleep(0.15)
            return "late"

        result = rm.bounded_map([0], slow, max_workers=1,
                                per_item_timeout=0.01)
        self.assertEqual(result.timed_out, [0])
        time.sleep(0.25)
        self.assertEqual(result.results, {}, "late worker must not mutate returned result")

    def test_rate_limit_backoff_is_bounded(self):
        sleeps = []
        self.assertEqual(rm.rate_limit_backoff(10, base_seconds=1, max_seconds=4,
                                               sleep=sleeps.append), 4.0)
        self.assertEqual(sleeps, [4.0])

    def test_cache_rejects_stale_evidence(self):
        now = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}

        def now_fn():
            return now["t"].isoformat()

        cache = rm.EvidenceCache(now_fn=now_fn)
        stamp = now_fn()
        cache.put("spy", rm.Evidence("yfinance", "SPY", stamp, stamp,
                                     (now["t"] + timedelta(minutes=1)).isoformat(),
                                     "v1", 100.0))
        self.assertIsNotNone(cache.get("spy"))
        now["t"] += timedelta(minutes=5)
        self.assertIsNone(cache.get("spy"))

    def test_deterministic_prefilter_blocks_ineligible(self):
        blocked = rm.deterministic_prefilter(
            symbol="X", bars=7, min_bars=12, quote_age_seconds=10,
            max_quote_age_seconds=300, spread_pct=0.01, max_spread_pct=0.5,
            new_risk=True, regime="RISK_OFF", session_open=True, cutoff_passed=False)
        self.assertFalse(blocked.eligible)
        self.assertEqual(blocked.gate, "history" if 7 < 12 else "regime")
        passed = rm.deterministic_prefilter(
            symbol="X", bars=20, min_bars=12, quote_age_seconds=10,
            max_quote_age_seconds=300, spread_pct=0.01, max_spread_pct=0.5,
            new_risk=False, regime="RISK_OFF", session_open=True, cutoff_passed=False)
        self.assertTrue(passed.eligible)


class ShortRoutingTests(unittest.TestCase):
    def _research(self, decision="Underweight", verdict="PUT"):
        return scr.BearishResearch("AMD", decision, verdict, "RISK_OFF", "unit-test")

    def test_bearish_classification_and_no_auto_short(self):
        research = self._research()
        self.assertTrue(research.is_bearish)
        self.assertFalse(research.automatic_short)

    def test_position_effect_distinguishes_all_four_intents(self):
        self.assertEqual(scr.position_effect_for(
            side="sell", owned_qty=0, direction="short", opening=True).position_effect, "open")
        self.assertEqual(scr.position_effect_for(
            side="sell", owned_qty=10, direction="long", opening=False).position_effect, "close")
        self.assertEqual(scr.position_effect_for(
            side="buy", owned_qty=-5, direction="short", opening=False).position_effect, "close")
        self.assertEqual(scr.position_effect_for(
            side="buy", owned_qty=0, direction="long", opening=True).position_effect, "open")

    def test_underweight_never_executes_short(self):
        result = scr.route_bearish_candidate(self._research())
        self.assertEqual(result["decision_class"], "rejected")
        self.assertEqual(result["orders_submitted"], 0)
        self.assertTrue(result["safety_markers"]["no_broker_write"])

    def test_technical_setup_validation_and_borrow_rejection(self):
        setup = {"setup": "short_breakdown", "price": 100}
        def validation(s, e, p):
            return True, "ok", {"ticker": "AMD", "entry": 100, "stop": 105,
                                "target": 90, "qty": 10}
        result = scr.route_bearish_candidate(
            self._research(), setup=setup, equity=100000,
            validate_setup=validation,
            check_eligibility=lambda p: {"eligible": False, "reason": "borrow not available"},
            check_risk=lambda p: {"approved": True, "reason": "ok"})
        self.assertEqual(result["decision_class"], "rejected")
        self.assertTrue(any(r["gate"] == "borrow" for r in result["rejections"]))

    def test_risk_check_and_shadow_candidate(self):
        setup = {"setup": "short_breakdown", "price": 100}
        plan = {"ticker": "AMD", "entry": 100, "stop": 105, "target": 90,
                "qty": 10, "r_r_ratio": 2.0}
        result = scr.route_bearish_candidate(
            self._research(), setup=setup, equity=100000,
            validate_setup=lambda s, e, p: (True, "ok", plan),
            check_eligibility=lambda p: {"eligible": True, "reason": "ok"},
            check_risk=lambda p: {"approved": True, "reason": "ok"})
        self.assertEqual(result["decision_class"], "shadow_candidate")
        self.assertEqual(result["orders_submitted"], 0)
        self.assertEqual(result["plan"]["r_r_ratio"], 2.0)

    def test_reward_to_risk_below_minimum_rejected(self):
        class Fail(Exception):
            pass
        result = scr.route_bearish_candidate(
            self._research(), setup={"setup": "short_breakdown"}, equity=100000,
            validate_setup=lambda s, e, p: (False, "rr below 1.5", None),
            check_eligibility=lambda p: {}, check_risk=lambda p: {})
        self.assertTrue(any("rr" in r["reason"] for r in result["rejections"]))

    def test_rejected_record_never_keeps_an_actionable_plan(self):
        result = scr.route_bearish_candidate(
            self._research(), setup={"setup": "short_breakdown"}, equity=100000,
            validate_setup=lambda s, e, p: (True, "ok", {"entry": 100, "qty": 5}),
            check_eligibility=lambda p: {"eligible": False, "reason": "no borrow"},
            check_risk=lambda p: {"approved": True, "reason": "ok"})
        self.assertEqual(result["decision_class"], "rejected")
        self.assertIsNone(result["plan"])

    def test_real_adapter_passes_broker_transport(self):
        from robinhood_tools import alpaca_paper, settings, short_eligibility
        fake_transport = object()
        with mock.patch.object(short_eligibility, "fetch_short_eligibility",
                               return_value={"eligible": False, "reason": "mock"}) as fetch, \
             mock.patch.object(settings, "load_env", return_value={}), \
             mock.patch.object(alpaca_paper.AlpacaPaperHttpTransport,
                               "from_values", return_value=fake_transport):
            adapter = scr.read_only_adapter()
            adapter["check_eligibility"]({"ticker": "AMD"})
        self.assertEqual(fetch.call_args.args[1], fake_transport)

    def test_authorization_is_never_derived(self):
        source = Path("scripts/short_candidate_router.py").read_text(encoding="utf-8")
        for token in ("derive_and_store", "place_signal_order", "auth_bootstrap",
                      "paper_flow", "open_intent", "gateway("):
            self.assertNotIn(token, source)


class VixEvidenceTests(unittest.TestCase):
    def _frame(self, as_of=None):
        class Index:
            def __getitem__(inner, i):
                return as_of

        class Iloc:
            def __getitem__(inner, i):
                return 27.3

        class Series:
            index = Index()
            iloc = Iloc()

            def dropna(inner):
                return inner

            def __len__(inner):
                return 1

        return {"Close": Series()}

    def test_correct_timestamps_and_freshness(self):
        retrieved = "2026-11-24T16:00:00+00:00"
        frame = self._frame(datetime(2026, 11, 24, 10, 0))
        ev = ve.build_evidence(frame, retrieved_at=retrieved, threshold=25.0)
        self.assertEqual(ev.value, 27.3)
        self.assertEqual(ev.market_as_of_source, "bar_index")
        self.assertEqual(ev.freshness, "fresh")
        self.assertEqual(ve.validate_evidence(
            ev, now="2026-11-24T16:00:00+00:00").value, 27.3)

    def test_missing_as_of_is_rejected(self):
        frame = self._frame(None)
        ev = ve.build_evidence(frame, retrieved_at="2026-11-24T16:00:00+00:00")
        self.assertEqual(ev.freshness, "unverified")
        with self.assertRaises(ve.VixEvidenceError):
            ve.validate_evidence(ev)

    def test_stale_evidence_rejected(self):
        frame = self._frame(datetime(2026, 11, 20, 10, 0))
        ev = ve.build_evidence(frame, retrieved_at="2026-11-24T16:00:00+00:00")
        self.assertEqual(ev.freshness, "stale")
        with self.assertRaises(ve.VixEvidenceError):
            ve.validate_evidence(ev)

    def test_fabricated_freshness_label_is_recomputed(self):
        frame = self._frame(datetime(2026, 11, 20, 10, 0))
        ev = ve.build_evidence(frame, retrieved_at="2026-11-24T16:00:00+00:00")
        forged = ve.VixEvidence(**{**ev.as_dict(), "freshness": "fresh"})
        with self.assertRaises(ve.VixEvidenceError):
            ve.validate_evidence(forged, now="2026-11-24T16:00:00+00:00")

    def test_mixed_offset_cache_comparison_is_normalized(self):
        stamp = "2026-11-24T15:30:00-05:00"  # 20:30 UTC, in the future
        evidence = rm.Evidence("yfinance", "SPY", stamp, stamp,
                               "2026-11-24T21:00:00+00:00", "v1", 1.0)
        self.assertFalse(evidence.is_fresh(now="2026-11-24T16:00:00+00:00"))

    def test_threshold_boundaries_unchanged(self):
        base = dict(premium=0, contracts=1, realized_pnl_today=0)
        option_entry_risk.validate_option_entry(vix=25.0, **base)
        with self.assertRaises(Exception):
            option_entry_risk.validate_option_entry(vix=25.01, **base)


class ChallengerTests(unittest.TestCase):
    def test_insufficient_oos_never_promotes(self):
        from scripts.risk_off_short_challenger import evaluate
        rows = [{"date": f"2026-01-{i+1:02d}", "regime": "RISK_OFF",
                 "r_multiple": 1.0, "borrow_days": 1} for i in range(10)]
        result = evaluate(rows)
        self.assertFalse(result["evidence_sufficient"])
        self.assertEqual(result["orders_submitted"], 0)
        self.assertIn("no promotion", result["decision"])


if __name__ == "__main__":
    unittest.main()
