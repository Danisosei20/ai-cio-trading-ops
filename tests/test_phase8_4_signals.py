"""Phase 8.4: long/short signal integrity, archive and capability classification."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from robinhood_tools import bar_validation, data_capability
from scripts import intraday_signal_factory as factory
from scripts import signal_archive


def bars(n=20, *, price=100.0, direction="up"):
    start = datetime(2026, 11, 24, 14, 30, tzinfo=timezone.utc)
    step = 0.1 if direction == "up" else -0.1
    return [{"t": (start + timedelta(minutes=5 * i)).isoformat(),
             "h": price + step * i + 1, "l": price + step * i - 1,
             "c": price + step * i, "v": 1000 + i} for i in range(n)]


class SetupTests(unittest.TestCase):
    def test_opening_range_breakdown(self):
        ok, why = bar_validation.short_breakdown(95, vwap=100, or_high=105, or_low=100)
        self.assertTrue(ok, why)

    def test_opening_range_reclaim(self):
        ok, why = bar_validation.long_reclaim(106, vwap=100, or_high=105, or_low=99)
        self.assertTrue(ok, why)

    def test_vwap_rejection_and_reclaim(self):
        self.assertFalse(bar_validation.short_breakdown(
            101, vwap=100, or_high=105, or_low=100)[0])
        self.assertFalse(bar_validation.long_reclaim(
            99, vwap=100, or_high=105, or_low=99)[0])

    def test_directional_identity(self):
        up = factory.make_candidate(symbol="QQQ", bars=bars(direction="up"),
                                    setup=factory.SHORT,
                                    now="2026-11-24T16:10:00+00:00")
        down = factory.make_candidate(symbol="QQQ", bars=bars(direction="down"),
                                      setup=factory.LONG,
                                      now="2026-11-24T16:10:00+00:00")
        self.assertEqual(up.direction, "short")
        self.assertEqual(up.validation_status, "rejected")
        self.assertEqual(down.direction, "long")
        self.assertEqual(down.validation_status, "rejected")

    def test_missing_setup_rejected_without_levels(self):
        result = factory.make_candidate(symbol="QQQ", bars=[],
                                        setup=factory.SHORT,
                                        now="2026-11-24T16:10:00+00:00")
        self.assertEqual(result.validation_status, "rejected")
        self.assertIsNone(result.invalidation_level)
        self.assertIsNone(result.planned_rr)

    def test_future_dated_evidence_rejected(self):
        result = factory.make_candidate(symbol="QQQ", bars=bars(),
                                        setup=factory.LONG,
                                        now="2026-11-24T12:00:00+00:00")
        self.assertEqual(result.validation_status, "rejected")
        self.assertIn("future", result.rejection_reason)

    def test_out_of_order_bars_rejected(self):
        rows = bars()
        rows[3], rows[4] = rows[4], rows[3]
        self.assertEqual(bar_validation.validate_bars(
            rows, now=datetime(2026, 11, 24, 16, 20, tzinfo=timezone.utc)).status,
            "rejected")

    def test_stale_signal_rejected(self):
        result = bar_validation.validate_bars(
            bars(), now=datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(result.status, "rejected")

    def test_symbol_identity_mismatch_in_lineage(self):
        from scripts import short_signal_lineage as lineage
        row = {"symbol": "AMD", "decision": "Sell", "signal_at": "2026-11-24T15:00:00Z"}
        result = lineage.audit(row, setup={"ticker": "NVDA", "setup": "short_breakdown"})
        self.assertEqual(result.rejection, "symbol_identity_mismatch")

    def test_unique_signal_id_and_evidence_hash(self):
        a = factory.make_candidate(symbol="QQQ", bars=bars(), setup=factory.LONG,
                                   now="2026-11-24T16:10:00+00:00")
        b = factory.make_candidate(symbol="QQQ", bars=bars(), setup=factory.LONG,
                                   now="2026-11-24T16:10:00+00:00")
        self.assertEqual(a.signal_id, b.signal_id)
        self.assertEqual(a.evidence_hash, b.evidence_hash)
        other = factory.make_candidate(symbol="QQQ", bars=bars(), setup=factory.LONG,
                                       now="2026-11-24T16:10:00+00:00", feed="sip")
        self.assertNotEqual(a.signal_id, other.signal_id)


class ArchiveTests(unittest.TestCase):
    def test_archive_manifest_and_replay(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cand = factory.make_candidate(symbol="QQQ", bars=bars(), setup=factory.LONG,
                                          now="2026-11-24T16:10:00+00:00")
            path = signal_archive.archive_signal(
                root, cand.as_dict(), bars=bars(),
                strategy_version=factory.STRATEGY_VERSION,
                now="2026-11-24T16:10:00+00:00")
            self.assertIsNotNone(path)
            ok, why = signal_archive.verify_archive(path)
            self.assertTrue(ok, why)
            rows = signal_archive.replay(root, "2026-11-24")
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["integrity"])

    def test_tampered_archive_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cand = factory.make_candidate(symbol="QQQ", bars=bars(), setup=factory.LONG,
                                          now="2026-11-24T16:10:00+00:00")
            path = signal_archive.archive_signal(
                root, cand.as_dict(), bars=bars(), strategy_version="v1",
                now="2026-11-24T16:10:00+00:00")
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["signal"]["direction"] = "short"
            path.write_text(json.dumps(doc), encoding="utf-8")
            ok, why = signal_archive.verify_archive(path)
            self.assertFalse(ok)
            self.assertIn("hash", why)


class CapabilityTests(unittest.TestCase):
    def test_sip_unsupported_blocks_breadth_strategy(self):
        report = data_capability.capability_report(data_capability.CapabilitySnapshot())
        self.assertEqual(report["strategies"]["consolidated_breadth"]["status"],
                         data_capability.UNSUPPORTED)
        self.assertIn("consolidated_breadth", report["requires_sip"])

    def test_iex_volume_never_consolidated(self):
        snap = data_capability.CapabilitySnapshot(feed="iex", volume_basis="iex_only")
        report = data_capability.capability_report(snap)
        self.assertEqual(report["snapshot"]["volume_basis"], "iex_only")
        self.assertFalse(report["strategies"]["daytrade_short"]["entry_capability_ok"]
                         is False)  # short uses IEX + eligibility, still supported
        self.assertEqual(report["snapshot"]["realtime_sip"], data_capability.UNSUPPORTED)

    def test_stale_capability_blocks_entries(self):
        snap = data_capability.CapabilitySnapshot(realtime_quotes=data_capability.STALE)
        row = data_capability.classify_strategy("daytrade_long", snap)
        self.assertEqual(row["status"], data_capability.UNSUPPORTED)
        self.assertFalse(row["entry_capability_ok"])


class ScoringTests(unittest.TestCase):
    def test_missing_factors_score_zero(self):
        from scripts.opportunity_scoring import score_candidate
        result = score_candidate({"symbol": "QQQ", "direction": "long"},
                                 factors={"setup_valid": True})
        self.assertEqual(result.score, 20.0)
        self.assertTrue(result.missing)
        self.assertFalse(result.evidence_complete)

    def test_score_never_authorizes(self):
        from scripts.opportunity_scoring import scoring_report
        report = scoring_report([])
        self.assertEqual(report["orders_submitted"], 0)
        self.assertIn("none", report["entry_permission"])


if __name__ == "__main__":
    unittest.main()
