import json
import tempfile
import unittest
from pathlib import Path

from scripts.market_state import market_payload


class MarketStateTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def _seed(self, symbol):
        d = self.root / "outputs/orchestrator/r1"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"bars_{symbol}.json").write_text(json.dumps({
            "symbol": symbol, "source": "yfinance", "generated_at": "2026-11-21T10:00:00-05:00",
            "bars": [{"ts": "2026-11-21T09:35:00-05:00", "open": 100, "high": 102,
                      "low": 99, "close": 101, "volume": 1000}],
        }))
        z = self.root / "outputs/zones/2026-11-21"
        z.mkdir(parents=True, exist_ok=True)
        (z / f"{symbol}.json").write_text(json.dumps({
            "generated_at": "2026-11-21T10:00:00-05:00", "price": 101,
            "profile": {"poc": 100.5, "val": 99.5, "vah": 101.5, "supply_nodes": [105.0],
                        "demand_nodes": [97.0], "high": 102, "low": 99, "vwap": 100.2},
            "verdict": {"verdict": "long_ok", "reasons": []},
        }))

    def _seed_auto(self, symbol, doc):
        a = self.root / f"outputs/paper/tradingagents/{symbol}/2026-11-21"
        a.mkdir(parents=True, exist_ok=True)
        (a / "auto.json").write_text(json.dumps(doc))
        return a

    def test_unavailable_on_missing_artifacts(self):
        p = market_payload(self.root, "NVDA", now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(p["status"], "unavailable")

    def test_ok_payload_and_every_level_has_source(self):
        self._seed("NVDA")
        p = market_payload(self.root, "NVDA", now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(p["status"], "ok")
        self.assertEqual(p["bars"][0]["close"], 101)
        for name, level in p["levels"].items():
            self.assertIn("source", level, name)

    def test_put_context_and_computed_targets(self):
        self._seed("NVDA")
        a = self.root / "outputs/paper/tradingagents/NVDA/2026-11-21"
        a.mkdir(parents=True, exist_ok=True)
        (a / "auto.json").write_text(json.dumps(
            {"action": "sell", "ticker": "NVDA", "reason": "bearish"}))
        p = market_payload(self.root, "NVDA", now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(p["direction"], "put")
        self.assertIn("computed", p["levels"]["stop"]["source"])
        self.assertIn("computed", p["levels"]["tp1"]["source"])
        # A bullish action must not flip to put; keep the call reading.
        (a / "auto.json").write_text(json.dumps(
            {"action": "buy", "ticker": "NVDA", "reason": "bullish"}))
        call = market_payload(self.root, "NVDA", now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(call["direction"], "call")
        self.assertLess(call["levels"]["stop"]["value"], call["levels"]["tp1"]["value"])

    def test_real_producer_auto_shapes_direction(self):
        """The real producer writes suffixed actions plus a decision field."""
        cases = (
            ({"action": "sell_placed", "decision": "Sell"}, "put"),
            ({"action": "dry_run_sell", "decision": "Sell"}, "put"),
            ({"action": "buy_placed", "decision": "Buy"}, "call"),
        )
        for doc, expected in cases:
            with self.subTest(doc=doc):
                self._seed("NVDA")
                self._seed_auto("NVDA", dict(doc, ticker="NVDA"))
                p = market_payload(self.root, "NVDA", now_iso="2026-11-21T10:20:00-05:00")
                self.assertEqual(p["direction"], expected)
                self.assertTrue(any(s.startswith("direction:outputs/paper/tradingagents")
                                    for s in p["sources"]), p["sources"])

    def test_producer_skip_has_no_directional_evidence(self):
        """A skip decision must not be read as a producer-backed CALL."""
        self._seed("NVDA")
        self._seed_auto("NVDA", {"action": "skip", "decision": "Hold", "ticker": "NVDA"})
        p = market_payload(self.root, "NVDA", now_iso="2026-11-21T10:20:00-05:00")
        self.assertIn("direction:computed-unknown", p["sources"])
        direction_sources = [s for s in p["sources"] if s.startswith("direction:")]
        self.assertEqual(direction_sources, ["direction:computed-unknown"])

    def test_producer_stop_and_target_fields_are_used(self):
        """auto.json writes stop/target (not stop_price/target_price)."""
        self._seed("NVDA")
        self._seed_auto("NVDA", {"action": "buy_placed", "decision": "Buy",
                                 "stop": "98", "target": "110", "ticker": "NVDA"})
        p = market_payload(self.root, "NVDA", now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(p["levels"]["stop"]["value"], 98.0)
        self.assertEqual(p["levels"]["tp1"]["value"], 110.0)
        self.assertIn("auto.json#stop", p["levels"]["stop"]["source"])
        self.assertIn("auto.json#target", p["levels"]["tp1"]["source"])
        self.assertNotIn("computed-2R-3R", p["levels"]["stop"]["source"])
        self.assertNotIn("computed-2R-3R", p["levels"]["tp1"]["source"])

    def test_legacy_stop_price_target_price_still_accepted(self):
        self._seed("NVDA")
        self._seed_auto("NVDA", {"action": "buy", "stop_price": 97, "target_price": 108})
        p = market_payload(self.root, "NVDA", now_iso="2026-11-21T10:20:00-05:00")
        self.assertEqual(p["levels"]["stop"]["value"], 97.0)
        self.assertEqual(p["levels"]["tp1"]["value"], 108.0)
