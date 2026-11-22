"""Tests for the read-only, fail-closed short-basket signal.

The proposal must rank the weakest names from the desk's own completed-session
bars, carry a verbatim source for every row, skip symbols with insufficient
sessions, pick the freshest run by mtime (never filename order), and add no
write route.
"""
import json
import math
import os
import tempfile
import unittest
from pathlib import Path

from scripts.short_basket import short_basket

GEN = "2026-11-22T10:00:00-05:00"
RUN = "universe-2026-11-22"
DAY1 = "2026-11-20"
DAY2 = "2026-11-21"


def _bars(closes_by_session):
    """Build an ordered bar list from ``{session_date: [closes...]}``."""
    bars = []
    for session, closes in closes_by_session.items():
        for i, close in enumerate(closes):
            bars.append({
                "ts": f"{session}T09:{35 + i:02d}:00-05:00",
                "open": close, "high": 1000, "low": 0,
                "close": close, "volume": 1000,
            })
    return bars


class ShortBasketTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def _write(self, rel, doc):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def _seed_bars(self, symbol, closes_by_session, *, run=RUN, generated_at=GEN):
        rel = f"outputs/orchestrator/{run}/bars_{symbol}.json"
        return self._write(rel, {
            "symbol": symbol,
            "bars": _bars(closes_by_session),
            "source": "yfinance",
            "generated_at": generated_at,
        })

    def test_unavailable_on_no_artifacts(self):
        payload = short_basket(self.root)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("reason", payload)

    def test_unavailable_on_malformed_bars(self):
        self._write(f"outputs/orchestrator/{RUN}/bars_X.json", {
            "symbol": "X", "bars": "not-a-list", "generated_at": GEN,
        })
        payload = short_basket(self.root)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("reason", payload)

    def test_rank_by_weakness(self):
        self._seed_bars("A", {DAY1: [100], DAY2: [80]})
        self._seed_bars("B", {DAY1: [100], DAY2: [95]})
        payload = short_basket(self.root)
        self.assertEqual(payload["status"], "ok")
        rows = payload["basket"]
        self.assertEqual([row["symbol"] for row in rows], ["A", "B"])
        self.assertEqual(rows[0]["change_pct"], -20.0)
        self.assertEqual(rows[1]["change_pct"], -5.0)

    def test_skips_symbols_with_too_few_sessions(self):
        self._seed_bars("A", {DAY1: [100], DAY2: [80]})
        self._seed_bars("B", {DAY2: [95]})  # one session only
        payload = short_basket(self.root)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual([row["symbol"] for row in payload["basket"]], ["A"])

    def test_latest_run_by_mtime(self):
        self._seed_bars("A", {DAY1: [100], DAY2: [50]}, run="universe-old")
        self._seed_bars("A", {DAY1: [100], DAY2: [80]}, run="universe-new")
        old = self.root / "outputs/orchestrator/universe-old"
        new = self.root / "outputs/orchestrator/universe-new"
        # Set directory mtimes after writing so file writes cannot overwrite them.
        os.utime(old, (1_700_000_000, 1_700_000_000))
        os.utime(new, (1_800_000_000, 1_800_000_000))
        payload = short_basket(self.root)
        self.assertEqual(payload["status"], "ok")
        row = payload["basket"][0]
        self.assertEqual(row["change_pct"], -20.0)
        self.assertIn("universe-new", row["source"])

    def test_every_row_has_source(self):
        self._seed_bars("A", {DAY1: [100], DAY2: [80]})
        payload = short_basket(self.root)
        row = payload["basket"][0]
        self.assertIsInstance(row["source"], str)
        self.assertTrue(row["source"].strip())
        self.assertIn(f"outputs/orchestrator/{RUN}/bars_A.json", row["source"])
        self.assertIn(GEN, row["source"])

    def test_finite_numbers(self):
        self._seed_bars("A", {DAY1: [100], DAY2: [80]})
        self._seed_bars("BAD-NAN", {DAY1: [100], DAY2: [float("nan")]})
        self._seed_bars("BAD-INF", {DAY1: [100], DAY2: [float("inf")]})
        self._seed_bars("BAD-ZERO", {DAY1: [100], DAY2: [0]})
        self._seed_bars("BAD-TEXT", {DAY1: [100], DAY2: ["not-a-number"]})
        payload = short_basket(self.root)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual([row["symbol"] for row in payload["basket"]], ["A"])
        for key in ("reference_price", "change_pct", "allocation", "shares", "notional"):
            self.assertTrue(math.isfinite(payload["basket"][0][key]), key)

    def test_returns_partial_basket_when_fewer_than_count(self):
        self._seed_bars("A", {DAY1: [100], DAY2: [80]})
        self._seed_bars("B", {DAY1: [100], DAY2: [95]})
        payload = short_basket(self.root, count=5)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["basket"]), 2)

    def test_payload_shape_and_sizing(self):
        self._seed_bars("A", {DAY1: [100], DAY2: [80]})
        payload = short_basket(self.root, capital=500.0, count=5)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["capital"], 500.0)
        self.assertEqual(payload["count"], 5)
        self.assertEqual(payload["generated_at"], GEN)
        row = payload["basket"][0]
        self.assertEqual(row["reference_price"], 80.0)
        self.assertEqual(row["allocation"], 100.0)
        self.assertEqual(row["shares"], 1.25)
        self.assertEqual(row["notional"], 100.0)
        self.assertEqual(payload["sources"], [row["source"]])

    def test_skips_row_when_sizing_overflows_to_non_finite(self):
        # A finite but subnormal close is a valid price, yet 100 / 5e-324
        # overflows to inf shares. Such a row must never reach the payload.
        self._seed_bars("A", {DAY1: [100], DAY2: [80]})
        self._seed_bars("TINY", {DAY1: [1e-323], DAY2: [5e-324]})
        payload = short_basket(self.root)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual([row["symbol"] for row in payload["basket"]], ["A"])
        self.assertEqual(payload["sources"], [payload["basket"][0]["source"]])
        for row in payload["basket"]:
            for key in ("shares", "notional"):
                self.assertTrue(math.isfinite(row[key]), key)

    def test_read_only_basket_routes(self):
        source = Path("scripts/agent_office.py").read_text(encoding="utf-8")
        self.assertIn('elif route == "/api/basket":', source)
        self.assertIn('elif route == "/basket":', source)
        self.assertNotIn("do_POST", source)


if __name__ == "__main__":
    unittest.main()
