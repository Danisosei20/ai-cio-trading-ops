"""Phase 8.5: immutable snapshots, freshness modes and scout/factory comparison."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from robinhood_tools import market_snapshot as snap
from robinhood_tools import signal_freshness as fresh
from scripts import scout_factory_compare as cmp

DATE = "2026-11-24"
NOW = "2026-11-24T20:00:00+00:00"  # 15:00 ET, regular session


def bars(n=12):
    start = datetime(2026, 11, 24, 19, 0, tzinfo=timezone.utc)
    return [{"t": (start + timedelta(minutes=5 * i)).isoformat(),
             "h": 101 + i * 0.1, "l": 100 + i * 0.1,
             "c": 100.5 + i * 0.1, "v": 1000 + i} for i in range(n)]


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def test_immutable_multi_cycle_snapshots(self):
        first = snap.write_snapshot(self.root, date=DATE, cycle_id="c1", symbol="QQQ",
                                    bars=bars(), provider="yfinance", feed="iex",
                                    timeframe="5m", now=NOW)
        second = snap.write_snapshot(self.root, date=DATE, cycle_id="c2", symbol="QQQ",
                                     bars=bars(13), provider="yfinance", feed="iex",
                                     timeframe="5m", now=NOW)
        self.assertNotEqual(first["path"], second["path"])
        ok, why = snap.verify_snapshot(Path(first["path"]))
        self.assertTrue(ok, why)
        self.assertEqual(first["evidence_sha256"],
                         json.loads(Path(first["path"]).read_text())["evidence_sha256"])
        rows = snap.replay_snapshots(self.root, DATE)
        self.assertEqual(len(rows), 2)

    def test_duplicate_cycle_rejected(self):
        snap.write_snapshot(self.root, date=DATE, cycle_id="c1", symbol="QQQ",
                            bars=bars(), provider="yfinance", feed="iex",
                            timeframe="5m", now=NOW)
        with self.assertRaises(snap.SnapshotError):
            snap.write_snapshot(self.root, date=DATE, cycle_id="c1", symbol="QQQ",
                                bars=bars(), provider="yfinance", feed="iex",
                                timeframe="5m", now=NOW)

    def test_tampered_bars_fail_hash(self):
        result = snap.write_snapshot(self.root, date=DATE, cycle_id="c1", symbol="QQQ",
                                     bars=bars(), provider="yfinance", feed="iex",
                                     timeframe="5m", now=NOW)
        path = Path(result["path"])
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["bars"][0]["c"] = 999
        path.write_text(json.dumps(doc), encoding="utf-8")
        ok, why = snap.verify_snapshot(path)
        self.assertFalse(ok)
        self.assertIn("hash", why)

    def test_path_traversal_rejected(self):
        for cycle, symbol in (("../evil", "QQQ"), ("c1", "../../QQQ")):
            with self.assertRaises(snap.SnapshotError):
                snap.snapshot_path(self.root, DATE, cycle, symbol)

    def test_signal_links_to_snapshot(self):
        result = snap.write_snapshot(self.root, date=DATE, cycle_id="c1", symbol="QQQ",
                                     bars=bars(), provider="yfinance", feed="iex",
                                     timeframe="5m", signal_id="sig-1", now=NOW)
        doc = snap.load_snapshot(Path(result["path"]))
        self.assertEqual(doc["signal_id"], "sig-1")

    def test_replay_integrity_flags_tampering(self):
        result = snap.write_snapshot(self.root, date=DATE, cycle_id="c1", symbol="QQQ",
                                     bars=bars(), provider="yfinance", feed="iex",
                                     timeframe="5m", now=NOW)
        path = Path(result["path"])
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["freshness"] = "fresh"
        path.write_text(json.dumps(doc), encoding="utf-8")
        rows = snap.replay_snapshots(self.root, DATE)
        self.assertFalse(rows[0]["integrity"])


class FreshnessTests(unittest.TestCase):
    def test_fresh_intraday_bar(self):
        result = fresh.evaluate(completed_bar=True, bars_present=True, mode="live", market_as_of="2026-11-24T19:55:00+00:00",
                                retrieved_at="2026-11-24T19:56:00+00:00",
                                now="2026-11-24T19:56:10+00:00")
        self.assertEqual(result.status, "fresh")
        self.assertTrue(result.usable_for_live_entry)

    def test_historical_replay_valid_while_not_live_eligible(self):
        result = fresh.evaluate(completed_bar=True, bars_present=True, mode="replay", market_as_of="2026-11-24T19:55:00+00:00",
                                retrieved_at="2026-11-24T19:56:00+00:00",
                                now="2026-11-24T22:00:00+00:00")
        self.assertEqual(result.status, "historically_valid")
        self.assertTrue(result.valid_for_replay)
        self.assertFalse(result.usable_for_live_entry)

    def test_weekend_and_holiday_rejected(self):
        for day in ("2026-11-28", "2026-11-26"):
            result = fresh.evaluate(completed_bar=True, bars_present=True, mode="live", market_as_of=f"{day}T15:00:00Z",
                                    retrieved_at=f"{day}T15:00:10Z")
            self.assertEqual(result.status, "rejected")

    def test_early_close_session(self):
        result = fresh.evaluate(completed_bar=True, bars_present=True, mode="live", market_as_of="2026-11-27T17:30:00Z",
                                retrieved_at="2026-11-27T17:30:10Z",
                                now="2026-11-27T17:30:20Z")
        self.assertEqual(result.session, "early_close")

    def test_future_and_out_of_order_rejected(self):
        result = fresh.evaluate(completed_bar=True, bars_present=True, mode="live", market_as_of="2026-11-24T21:30:00Z",
                                retrieved_at="2026-11-24T19:00:00Z")
        self.assertEqual(result.status, "rejected")
        self.assertFalse(result.usable_for_live_entry)

    def test_unsupported_capability_blocks(self):
        result = fresh.evaluate(completed_bar=True, bars_present=True, mode="live", market_as_of="2026-11-24T19:55:00Z",
                                retrieved_at="2026-11-24T19:56:00Z",
                                now="2026-11-24T19:56:10Z", capability="UNSUPPORTED")
        self.assertEqual(result.status, "unsupported")
        self.assertFalse(result.usable_for_live_entry)

    def test_post_close_analysis_only(self):
        result = fresh.evaluate(completed_bar=True, bars_present=True, mode="post_close", market_as_of="2026-11-24T20:00:00Z",
                                retrieved_at="2026-11-24T20:00:10Z",
                                now="2026-11-24T22:00:00Z")
        self.assertFalse(result.usable_for_live_entry)
        self.assertTrue(result.valid_for_replay)


class ComparisonTests(unittest.TestCase):
    def test_short_rule_discrepancy_is_defect(self):
        # price below VWAP but only below OR high, not OR low.
        row = cmp.compare(symbol="QQQ", price=100, vwap=101, or_high=105,
                          or_low=100, bars=12, now=NOW)
        self.assertEqual(row.scout_setup, "short_breakdown")
        self.assertEqual(row.classification, "implementation_defect")
        self.assertIn("opening-range low", row.reason)

    def test_agreement_when_both_rules_accept(self):
        row = cmp.compare(symbol="QQQ", price=99, vwap=101, or_high=105,
                          or_low=100, bars=12, now=NOW)
        self.assertEqual(row.classification, "agreement")

    def test_no_order_path(self):
        source = Path(cmp.__file__).read_text(encoding="utf-8")
        for token in ("place_signal_order(", "submit_order(", "place_long_option("):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
