"""Tests for the read-only loaded-symbol list derived from desk artifacts."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.market_state import universe_symbols

OFFICE_PY = Path("scripts/agent_office.py")
NOW_ISO = "2026-11-22T10:20:00-05:00"


class UniverseSymbolsTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def _seed_bars(self, symbol):
        d = self.root / "outputs/orchestrator/r1"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"bars_{symbol}.json").write_text(json.dumps({
            "symbol": symbol, "bars": [{"close": 101}],
        }))

    def _seed_zone(self, symbol):
        d = self.root / "outputs/zones/2026-11-22"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{symbol}.json").write_text(json.dumps({
            "ticker": symbol, "profile": {"poc": 100.5},
        }))

    def test_unavailable_when_no_artifacts(self):
        p = universe_symbols(self.root, now_iso=NOW_ISO)
        self.assertEqual(p["status"], "unavailable")

    def test_universe_requires_both_artifacts(self):
        self._seed_bars("SYM_A")
        self._seed_zone("SYM_A")
        self._seed_bars("SYM_B")  # bars only
        self._seed_zone("SYM_C")  # zones only
        p = universe_symbols(self.root, now_iso=NOW_ISO)
        self.assertEqual(p["status"], "ok")
        self.assertEqual(p["symbols"], ["SYM_A"])
        self.assertEqual(p["count"], 1)
        self.assertEqual(p["generated_at"], NOW_ISO)
        self.assertIsInstance(p["sources"], list)
        self.assertTrue(p["sources"])

    def test_read_only_symbols_route(self):
        src = OFFICE_PY.read_text(encoding="utf-8")
        self.assertIn('"/api/symbols"', src)
        self.assertNotIn("do_POST", src)


if __name__ == "__main__":
    unittest.main()
