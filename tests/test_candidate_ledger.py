"""Phase 2 PR 4: candidate/opinion/option-scan/risk-decision ledger tests."""
from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from robinhood_tools.database import DATABASE_SCHEMA_VERSION, CioDatabase
from robinhood_tools.errors import PolicyViolation


class CandidateLedgerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = CioDatabase(Path(self.temp.name) / "cio.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_version_7(self):
        self.assertEqual(DATABASE_SCHEMA_VERSION, 7)

    def test_candidate_lifecycle(self):
        rec = self.db.record_candidate("c1", "nvda", "buy", {"source": "debate"})
        self.assertEqual(rec["status"], "discovered")
        self.assertEqual(rec["symbol"], "NVDA")
        for status in ("researching", "debating", "options_scanning", "risk_review", "approved"):
            rec = self.db.update_candidate_status("c1", status)
            self.assertEqual(rec["status"], status)
        with self.assertRaises(PolicyViolation):
            self.db.update_candidate_status("c1", "flying")
        with self.assertRaises(PolicyViolation):
            self.db.record_candidate("c1", "NVDA", "buy")
        with self.assertRaises(PolicyViolation):
            self.db.record_candidate("c2", "NVDA", "maybe")

    def test_opinions_and_timeline(self):
        self.db.record_candidate("c1", "NVDA", "buy")
        self.db.record_opinion("c1", "bull", "bullish", 88, {"target": 250})
        self.db.record_opinion("c1", "bear", "bearish", 39, {"risk": "resistance"})
        self.db.record_opinion("c1", "red", "challenge", 48, {})
        timeline = self.db.candidate_timeline("c1")
        self.assertEqual(len(timeline["opinions"]), 3)
        self.assertEqual(timeline["opinions"][0]["agent"], "bull")
        with self.assertRaises(PolicyViolation):
            self.db.record_opinion("c1", "oracle", "bullish", 50)
        with self.assertRaises(PolicyViolation):
            self.db.record_opinion("c1", "bull", "bullish", 101)
        with self.assertRaises(PolicyViolation):
            self.db.record_opinion("nope", "bull", "bullish", 50)

    def test_option_candidates_and_selection(self):
        self.db.record_candidate("c1", "NVDA", "buy")
        self.db.record_option_candidate(
            "c1", option_symbol="NVDA261106C00240000", strike=Decimal("240"),
            expiry="2026-11-06", right="call", bid=Decimal("3.80"),
            ask=Decimal("3.95"), spread_pct=Decimal("0.039"),
            iv=Decimal("0.38"), delta=Decimal("0.46"),
            open_interest=2000, volume=500)
        self.db.record_option_candidate(
            "c1", option_symbol="NVDA261106C00250000", strike=Decimal("250"),
            expiry="2026-11-06", right="call", bid=Decimal("0.10"),
            ask=Decimal("0.50"), spread_pct=Decimal("1.33"),
            open_interest=5, volume=2, rejection_reasons=["spread too wide"])
        self.db.mark_option_selected("c1", "NVDA261106C00240000")
        timeline = self.db.candidate_timeline("c1")
        self.assertEqual(len(timeline["option_candidates"]), 2)
        selected = [o for o in timeline["option_candidates"] if o["selected"]]
        self.assertEqual(len(selected), 1)
        with self.assertRaises(PolicyViolation):
            self.db.mark_option_selected("c1", "MISSING")
        with self.assertRaises(PolicyViolation):
            self.db.record_option_candidate(
                "nope", option_symbol="X", strike=Decimal("1"), expiry="2026-11-06",
                right="call", bid=Decimal("1"), ask=Decimal("2"),
                spread_pct=Decimal("0.5"))

    def test_risk_decisions(self):
        self.db.record_candidate("c1", "NVDA", "buy")
        self.db.record_risk_decision("r1", "c1", approved=True,
                                     failed_rules=[], results=[{"name": "mode", "passed": True}])
        self.db.record_risk_decision("r2", "c1", approved=False,
                                     failed_rules=["confidence"],
                                     results=[{"name": "confidence", "passed": False}])
        timeline = self.db.candidate_timeline("c1")
        self.assertEqual(len(timeline["risk_decisions"]), 2)
        self.assertEqual(timeline["risk_decisions"][0]["approved"], 1)
        with self.assertRaises(PolicyViolation):
            self.db.record_risk_decision("r1", "c1", approved=True,
                                         failed_rules=[], results=[])
        with self.assertRaises(PolicyViolation):
            self.db.candidate_timeline("ghost")

    def test_risk_orphan_reports_unknown_candidate(self):
        with self.assertRaises(PolicyViolation) as ctx:
            self.db.record_risk_decision("rx", "ghost", approved=False,
                                         failed_rules=[], results=[])
        self.assertIn("Unknown candidate", str(ctx.exception))

    def test_unknown_candidate_status_update(self):
        with self.assertRaises(PolicyViolation):
            self.db.update_candidate_status("ghost", "rejected")

    def test_duplicate_option_symbol_rejected(self):
        self.db.record_candidate("c1", "NVDA", "buy")
        kwargs = dict(option_symbol="NVDA261106C00240000", strike=Decimal("240"),
                      expiry="2026-11-06", right="call", bid=Decimal("3.80"),
                      ask=Decimal("3.95"), spread_pct=Decimal("0.039"))
        self.db.record_option_candidate("c1", **kwargs)
        with self.assertRaises(PolicyViolation):
            self.db.record_option_candidate("c1", **kwargs)

    def test_learning_checkpoint_lifecycle(self):
        self.db.schedule_learning("rec1", {1: "2026-10-08", 5: "2026-10-14"})
        due = self.db.due_learning_checkpoints("2026-10-08")
        self.assertEqual(len(due), 1)
        self.db.complete_learning_checkpoint("rec1", 1)
        self.assertEqual(self.db.due_learning_checkpoints("2026-10-08"), [])
        with self.assertRaises(PolicyViolation):
            self.db.complete_learning_checkpoint("rec1", 1)

    def test_approval_record_carries_order_id(self):
        from robinhood_tools.approvals import ApprovalRecord
        rec = ApprovalRecord(approval_id="a", review_id="r", order_fingerprint="f",
                             account_id="x", symbol="NVDA", created_at="t", expires_at="e")
        self.assertIsNone(rec.order_id)

    def test_audit_export_includes_candidate_ledger(self):
        self.db.record_candidate("c1", "NVDA", "buy")
        self.db.record_opinion("c1", "bull", "bullish", 80, {})
        self.db.record_risk_decision("r1", "c1", approved=True,
                                     failed_rules=[], results=[])
        export = self.db.audit_export()
        for table in ("trade_candidates", "agent_opinions",
                      "option_candidates", "risk_decisions"):
            self.assertIn(table, export)
        self.assertEqual(len(export["trade_candidates"]), 1)
        self.assertEqual(len(export["agent_opinions"]), 1)

    def test_eligible_purchase(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from robinhood_tools.universe import Sp500Snapshot

        fresh = Sp500Snapshot(frozenset({"NVDA"}), datetime.now(ZoneInfo("America/New_York")).isoformat(),
                              "https://example.com/sp500",
                              index_etfs=frozenset({"SPY", "QQQ", "DIA", "IWM", "XLK", "XLF"}))
        fresh.require_eligible_purchase("nvda")  # case-insensitive member
        fresh.require_eligible_purchase("SPY")  # allowlisted ETF
        for good in ("DIA", "IWM", "XLK", "XLF"):
            fresh.require_eligible_purchase(good)
        with self.assertRaises(PolicyViolation):
            fresh.require_eligible_purchase("TLT")  # not listed anywhere
        stale = Sp500Snapshot(frozenset({"NVDA"}), "2020-01-01T00:00:00+00:00",
                              "https://example.com/sp500", index_etfs=frozenset({"SPY"}))
        with self.assertRaises(PolicyViolation):
            stale.require_eligible_purchase("SPY")  # stale evidence fails even ETFs
        plain = Sp500Snapshot(frozenset({"NVDA"}), datetime.now(ZoneInfo("America/New_York")).isoformat(),
                              "https://example.com/sp500")
        with self.assertRaises(PolicyViolation):
            plain.require_eligible_purchase("SPY")  # no allowlist, no entry

    def test_list_trade_candidates_order(self):
        self.db.record_candidate("c1", "NVDA", "buy")
        self.db.record_candidate("c2", "AAPL", "sell")
        rows = self.db.list_trade_candidates(10)
        self.assertEqual([r["candidate_id"] for r in rows], ["c2", "c1"])
        self.assertEqual(len(self.db.list_trade_candidates(1)), 1)


if __name__ == "__main__":
    unittest.main()
