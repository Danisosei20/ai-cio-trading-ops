from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.paper_session_report import summarise_paper_sessions


def fill(symbol, side, qty, price, day):
    return {
        "symbol": symbol,
        "side": side,
        "quantity": str(qty),
        "price": str(price),
        "fee": "0",
        "filled_at": f"{day}T15:10:00Z",
    }


class PaperMetricsTest(unittest.TestCase):
    def test_empty_inputs_are_zero_sample_and_do_not_claim_available_evidence(self):
        report = summarise_paper_sessions([], [], {"evaluated": []})

        self.assertEqual(report["sessions"]["observed_days"], 0)
        self.assertEqual(report["closed_round_trips"]["count"], 0)
        self.assertEqual(report["pnl"]["win_rate"], 0.0)
        self.assertEqual(report["exits"]["historical_reason_status"], "unavailable")
        self.assertEqual(report["slippage"]["status"], "unavailable")

    def test_closed_and_open_positions_have_separate_counts_and_pnl(self):
        fills = [
            fill("NVDA261218C00240000", "buy", 1, "5.00", "2026-11-18"),
            fill("NVDA261218C00240000", "sell", 1, "6.00", "2026-11-19"),
            fill("NOW270115P00090000", "buy", 3, "7.95", "2026-11-20"),
        ]
        guard = {"evaluated": [{"symbol": "NOW270115P00090000", "signal": "HOLD"}]}

        report = summarise_paper_sessions(fills, [], guard)

        self.assertEqual(report["closed_round_trips"]["count"], 1)
        self.assertEqual(report["open_positions"]["count"], 1)
        self.assertEqual(report["pnl"]["net_realized"], 100.0)
        self.assertEqual(report["pnl"]["wins"], 1)

    def test_block_counts_use_explicit_reasons_and_iv_evidence(self):
        candidates = [
            {"status": "risk_review", "created_at": "2026-11-20T10:00:00Z",
             "payload": json.dumps({"source": "options-scout", "iv": "0.52"})},
            {"status": "rejected", "created_at": "2026-11-20T10:01:00Z",
             "payload": json.dumps({"reason": "reward/risk 1.2:1 below 2:1"})},
            {"status": "rejected", "created_at": "2026-11-20T10:02:00Z",
             "payload": json.dumps({"reason": "VIX above 25; regime skip"})},
        ]

        blocks = summarise_paper_sessions([], candidates, {"evaluated": []})["blocks"]

        self.assertEqual(blocks["iv_cap"]["observed"], 1)
        self.assertEqual(blocks["iv_cap"]["explicit_rejections"], 0)
        self.assertEqual(blocks["reward_risk"]["observed"], 1)
        self.assertEqual(blocks["regime_vix"]["observed"], 1)

    def test_iv_evidence_is_not_double_counted(self):
        candidates = [{
            "status": "rejected", "created_at": "2026-11-20T10:00:00Z",
            "payload": json.dumps({"source": "options-scout", "iv": "0.52",
                                    "reason": "quoted IV exceeds cap"}),
        }]
        iv = summarise_paper_sessions([], candidates, {"evaluated": []})["blocks"]["iv_cap"]
        self.assertEqual(iv["observed"], 1)
        self.assertEqual(iv["explicit_rejections"], 1)

    def test_exit_reasons_are_only_reported_when_guard_observed_them(self):
        guard = {"evaluated": [
            {"symbol": "A", "signal": "STOP"},
            {"symbol": "B", "signal": "TRAIL"},
            {"symbol": "C", "signal": "HOLD"},
        ]}

        exits = summarise_paper_sessions([], [], guard)["exits"]

        self.assertEqual(exits["current_guard_signals"]["STOP"], 1)
        self.assertEqual(exits["current_guard_signals"]["TRAIL"], 1)
        self.assertEqual(exits["current_guard_signals"]["HOLD"], 1)
        self.assertEqual(exits["historical_reason_status"], "unavailable")

    def test_guard_positions_are_current_position_source_when_present(self):
        guard = {"evaluated": [
            {"symbol": "NOW", "signal": "HOLD"},
            {"symbol": "TGT", "signal": "HOLD"},
        ]}

        positions = summarise_paper_sessions([], [], guard)["open_positions"]

        self.assertEqual(positions["count"], 2)
        self.assertEqual(positions["ledger_unmatched_count"], 0)


class PaperReportCliTest(unittest.TestCase):
    def test_cli_reads_sources_and_writes_only_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "paper.db"
            guard = root / "guard.json"
            out = root / "report.json"
            conn = sqlite3.connect(db)
            conn.executescript("""
                create table order_fills (symbol text, side text, quantity text,
                    price text, fee text, filled_at text);
                create table trade_candidates (status text, symbol text,
                    created_at text, payload text);
            """)
            conn.execute("insert into order_fills values (?, ?, ?, ?, ?, ?)",
                         ("SPY", "buy", "1", "500", "0", "2026-11-20T15:00:00Z"))
            conn.commit()
            conn.close()
            guard.write_text(json.dumps({"evaluated": []}), encoding="utf-8")

            from scripts.paper_session_report import main
            self.assertEqual(main(["--db", str(db), "--guard", str(guard),
                                   "--output", str(out)]), 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["open_positions"]["count"], 1)
            self.assertEqual(report["closed_round_trips"]["count"], 0)

    def test_cli_rejects_output_aliasing_a_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "paper.db"
            conn = sqlite3.connect(db)
            conn.execute("create table order_fills (symbol text, side text, quantity text, price text, fee text, filled_at text)")
            conn.execute("create table trade_candidates (status text, symbol text, created_at text, payload text)")
            conn.commit()
            conn.close()
            from scripts.paper_session_report import main
            with self.assertRaises(SystemExit):
                main(["--db", str(db), "--guard", str(db), "--output", str(db)])


if __name__ == "__main__":
    unittest.main()
