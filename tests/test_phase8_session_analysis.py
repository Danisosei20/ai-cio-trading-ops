"""Phase 8: read-only session analysis, point-in-time replay, dashboard safety."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts import missed_opportunity_analysis as moa
from scripts import opportunity_analysis as oa
from scripts.daily_trading_review import _fills, _scan_stats, build_review, markdown
from scripts.missed_opportunity_analysis import (analyze, analyst_bias,
                                                 classify_rejection, hypothetical,
                                                 is_automatic_short, load_signals)

DATE = "2026-11-24"
ASOF = "2026-11-24T11:42:00-05:00"


def write_json(path: Path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


class ClassificationTests(unittest.TestCase):
    def test_risk_off_rejection_classification(self):
        self.assertEqual(classify_rejection("regime RISK_OFF: new risk blocked desk-wide"),
                         "regime_policy")
        self.assertEqual(classify_rejection("option entry blocked: VIX 27.4 above 25"),
                         "vix_policy")

    def test_technical_failure_is_not_a_market_veto(self):
        self.assertEqual(classify_rejection("error: ModuleNotFoundError: tradingagents"),
                         "technical_failure")
        self.assertEqual(classify_rejection("judge failed (rc=1)"), "technical_failure")

    def test_no_position_held_is_structural(self):
        self.assertEqual(classify_rejection("bearish (Sell) but no position held"),
                         "no_short_entry_path")

    def test_long_and_short_candidate_classification(self):
        self.assertEqual(analyst_bias("Overweight"), "long")
        self.assertEqual(analyst_bias("Underweight"), "bearish_research")
        self.assertEqual(analyst_bias("Hold"), "neutral")

    def test_underweight_never_becomes_short(self):
        for decision in ("Sell", "Underweight", "BUY"):
            self.assertFalse(is_automatic_short(decision))


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        write_json(self.root / "outputs/daytrade" / f"{DATE}.json", {
            "date": DATE,
            "setups": [{"ticker": "XYZ", "direction": "short", "setup": "short_breakdown",
                        "price": 100, "vwap": 102, "or_high": 105, "or_low": 99}],
        })
        write_json(self.root / f"outputs/paper/tradingagents/XYZ/{DATE}/auto.json", {
            "ticker": "XYZ", "decision": "Sell", "desk_verdict": "PUT",
            "action": "skip", "reason": "option entry blocked: VIX 27.4 above 25",
        })

    def tearDown(self):
        self.t.cleanup()

    def test_missing_quote_never_invents_levels(self):
        rows, gaps = load_signals(self.root, DATE)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["price"])
        self.assertIsNone(rows[0]["entry"])
        report = analyze(self.root, DATE, as_of=ASOF, session_complete=False,
                         post_bars=None, cost_bps=5)
        row = report["candidates"][0]
        self.assertEqual(row["point_in_time_price"], 100)  # from setup artifact
        self.assertEqual(row["hypothetical_outcome"]["status"], "not_determinable")
        self.assertEqual(row["final_classification"], "valid_policy_rejection")

    def test_missing_research_environment_reports_gap(self):
        with tempfile.TemporaryDirectory() as td:
            rows, gaps = load_signals(Path(td), DATE)
        self.assertEqual(rows, [])
        self.assertTrue(any("missing research artifact root" in g for g in gaps))

    def test_stale_post_bars_are_ignored(self):
        post = {"XYZ": [{"ts": "2026-11-24T09:00:00-05:00", "close": 110}]}
        report = analyze(self.root, DATE, as_of=ASOF, session_complete=False,
                         post_bars=post, cost_bps=5)
        self.assertEqual(report["candidates"][0]["subsequent_movement"]["status"],
                         "unavailable")

    def test_valid_point_in_time_replay(self):
        write_json(self.root / f"outputs/paper/tradingagents/XYZ/{DATE}/auto.json", {
            "ticker": "XYZ", "decision": "Sell", "desk_verdict": "PUT",
            "action": "skip", "reason": "regime RISK_OFF: new risk blocked desk-wide",
            "entry": 100, "stop": 105, "target": 90, "price_as_of": ASOF,
        })
        post = {"XYZ": [
            {"ts": "2026-11-24T12:00:00-05:00", "close": 99},
            {"ts": "2026-11-24T14:00:00-05:00", "close": 90},
        ]}
        report = analyze(self.root, DATE, as_of=ASOF, session_complete=False,
                         post_bars=post, cost_bps=5)
        row = report["candidates"][0]
        self.assertEqual(row["hypothetical_outcome"]["status"], "hypothetical")
        self.assertGreater(row["hypothetical_outcome"]["r_multiple_after_cost"], 0)
        self.assertEqual(row["final_classification"], "hypothetical_outcome")
        self.assertTrue(row["hypothetical_outcome"]["note"].startswith("simulation"))

    def test_no_lookahead_in_entry_levels(self):
        # Post-signal prices cannot overwrite recorded entry/stop/target.
        post = {"XYZ": [{"ts": "2026-11-24T15:00:00-05:00", "close": 500}]}
        report = analyze(self.root, DATE, as_of=ASOF, session_complete=False,
                         post_bars=post, cost_bps=5)
        self.assertIsNone(report["candidates"][0]["proposed_entry"])
        self.assertEqual(report["candidates"][0]["subsequent_movement"]["status"], "ok")

    def test_incomplete_history_is_not_determinable(self):
        result = hypothetical(100, 105, 90, {"status": "unavailable"}, 5)
        self.assertEqual(result["status"], "not_determinable")

    def test_ambiguous_intrabar_path_is_uncertain(self):
        post = {"status": "ok", "first_post_signal_close": 100,
                "last_post_signal_close": 100}
        result = hypothetical(100, 105, 90, post, 5)
        self.assertEqual(result["status"], "not_determinable")

    def test_shadow_and_incorrect_interpreter_never_imported(self):
        source = Path(moa.__file__).read_text(encoding="utf-8")
        for token in ("alpaca_paper", "place_signal_order", "import tradingagents",
                      "paper_flow", "submit_order", "from robinhood_tools",
                      "daily_trading_review"):
            self.assertNotIn(token, source)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        write_json(self.root / "outputs/daytrade" / f"{DATE}.json", {
            "setups": [{"ticker": "XYZ", "direction": "short"}]})
        log = self.root / "outputs/intraday.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            f"{DATE}T10:00:00-05:00 === cycle 1 at 10:00 ET ===\n"
            f"{DATE}T10:45:00-05:00 new setups: ['XYZ']\n"
            f"{DATE}T11:40:00-05:00 judge completed for ['XYZ']\n",
            encoding="utf-8")
        write_json(self.root / f"outputs/paper/tradingagents/XYZ/{DATE}/auto.json", {
            "ticker": "XYZ", "decision": "Sell", "desk_verdict": "PUT",
            "action": "skip", "reason": "bearish (Sell) but no position held"})

    def tearDown(self):
        self.t.cleanup()

    def test_accurate_daily_scan_counts(self):
        stats = _scan_stats(self.root, DATE)
        self.assertEqual(stats["cycles"], 1)
        self.assertEqual(stats["symbols_scanned"], ["XYZ"])
        self.assertEqual(stats["setups_recorded"], 1)
        self.assertEqual(stats["setups_by_direction"], {"short": 1})
        self.assertEqual(stats["judge_completed_batches"], 1)

    def test_session_cutoff_and_incomplete_report(self):
        review = build_review(self.root, DATE, as_of=ASOF, session_complete=False)
        self.assertFalse(review["session_complete"])
        self.assertIn("INCOMPLETE", markdown(review))
        self.assertEqual(review["orders_submitted"], 0)

    def test_shadow_and_real_pnl_are_separate(self):
        review = build_review(self.root, DATE, as_of=ASOF, session_complete=False)
        perf = review["long_short_performance"]
        self.assertEqual(perf["realized_paper"]["filled_orders"], 0)
        self.assertIn("zero realized", perf["realized_paper"]["statistics"])
        self.assertIn("not realized profit", perf["shadow"]["note"])
        self.assertNotIn("combined_pnl", perf)
        self.assertNotIn("total_pnl", perf)

    def test_duplicate_report_generation_is_idempotent(self):
        from scripts.daily_trading_review import main
        out = self.root / "outputs/reports/daily"
        for _ in range(2):
            self.assertEqual(main(["--date", DATE, "--root", str(self.root),
                                   "--as-of", ASOF, "--out-dir", str(out)]), 0)
        names = sorted(p.name for p in out.iterdir())
        self.assertEqual(names, [f"{DATE}-trading-review.json",
                                 f"{DATE}-trading-review.md"])

    def test_broker_ledger_read_is_read_only(self):
        # No DB at all -> empty, no exception, no file created.
        self.assertEqual(_fills(self.root, DATE), [])
        self.assertFalse((self.root / "outputs/paper/cio.db").exists())


class DashboardTests(unittest.TestCase):
    def test_reader_unavailable_on_empty_root(self):
        with tempfile.TemporaryDirectory() as td:
            payload = oa.opportunity_analysis_payload(Path(td))
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["orders_submitted"], 0)

    def test_reader_normalizes_generated_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_json(root / "outputs/reports/daily" / f"{DATE}-trading-review.json", {
                "generated_at": ASOF, "session_date": DATE, "as_of": ASOF,
                "session_complete": False, "entry_permission": "none",
                "enforcement_enabled": False,
                "market_conditions": {"regimes_recorded": ["RISK_OFF"],
                                      "vix_values_in_rejections": [27.4]},
                "trading_activity": {"symbols_scanned": ["XYZ"], "cycles": 1,
                                     "setups_recorded": 1, "judge_failures": 0,
                                     "cycle_details": ["cycle 1"]},
                "trade_decisions": {"long_bias": 0, "bearish_research": 1,
                                    "approved_entries": 0, "valid_policy_rejections": 1,
                                    "technical_failures": 0, "shadow_rows": 1},
                "risk_management": {"vix_blocks": 0, "regime_blocks": 0,
                                    "no_short_path": 1, "emergency_kill": "off"},
                "long_short_performance": {"realized_paper": {"filled_orders": 0}},
                "missed_opportunities": {"rows": [{
                    "symbol": "XYZ", "analyst_bias": "bearish_research",
                    "rejection_reason": "bearish (Sell) but no position held",
                    "rejection_category": "no_short_entry_path",
                    "final_classification": "valid_policy_rejection",
                    "hypothetical_outcome": {"status": "not_determinable"},
                    "data_quality": "partial"}]},
            })
            payload = oa.opportunity_analysis_payload(root)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["summary"]["actual_fills"], 0)
        self.assertEqual(payload["summary"]["risk_blocked"], 1)
        self.assertEqual(len(payload["opportunities"]), 1)

    def test_dashboard_stays_read_only_and_safe(self):
        html = Path("web/agent-office/trading.html").read_text(encoding="utf-8")
        src = Path("scripts/agent_office.py").read_text(encoding="utf-8")
        self.assertIn('id="opportunity-analysis"', html)
        self.assertIn("/api/opportunity-analysis", html)
        self.assertIn("esc(", html)
        self.assertIn('route == "/api/opportunity-analysis"', src)
        self.assertNotIn("do_POST", src)
        self.assertNotIn("place_signal_order", html)

    def test_analysis_scripts_submit_no_orders(self):
        for rel in ("scripts/missed_opportunity_analysis.py",
                    "scripts/daily_trading_review.py",
                    "scripts/opportunity_analysis.py"):
            text = Path(rel).read_text(encoding="utf-8")
            for forbidden in ("place_signal_order", "submit_order", "do_POST",
                              "import alpaca_paper", "from robinhood_tools.alpaca_paper"):
                self.assertNotIn(forbidden, text, rel)


if __name__ == "__main__":
    unittest.main()
