"""Liquid signal cache refresh must be real, validated and fail-closed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import liquid_signals


class LiquidSignalsRefreshTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.cache = Path(self.t.name) / "liquid" / "latest.json"
        self.patcher = mock.patch.object(liquid_signals, "SIGNALS_PATH", self.cache)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def payload(self):
        return {
            "news": {"items": [], "error": "MARKETAUX_API_TOKEN not configured"},
            "positioning": {
                "long_heavy_crowd": [{"symbol": "NASDAQ:GPN", "put_call_volume_ratio": 1.7}],
                "short_heavy_crowd": [{"symbol": "CMG", "put_call_volume_ratio": 0.4}],
            },
            "leaderboard": [{"symbol": "NASDAQ:TSN", "changePercent": 3.2}],
        }

    def test_refresh_from_payload_writes_cache(self):
        liquid_signals.refresh_from_payload(self.payload())
        self.assertTrue(self.cache.exists())
        saved = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(saved["source"], "tradingview_mcp")
        self.assertEqual(saved["signals"]["crowded_longs_smart_money_fading"], ["GPN"])
        self.assertEqual(saved["signals"]["crowded_shorts_smart_money_buying"], ["CMG"])
        self.assertEqual(saved["leaderboard"][0]["symbol"], "TSN")

    def test_refresh_from_payload_strips_exchange_prefix(self):
        saved = liquid_signals.refresh_from_payload(self.payload())
        self.assertEqual(saved["positioning"]["long_heavy_crowd"][0]["symbol"], "GPN")

    def test_refresh_from_payload_rejects_empty_positioning(self):
        p = self.payload()
        p["positioning"] = {"long_heavy_crowd": [], "short_heavy_crowd": []}
        with self.assertRaises(ValueError):
            liquid_signals.refresh_from_payload(p)
        self.assertFalse(self.cache.exists())

    def test_refresh_from_payload_skips_malformed_entries(self):
        p = self.payload()
        p["positioning"]["long_heavy_crowd"] = [
            {"nope": 1}, "GPN", {"symbol": "  "}]
        saved = liquid_signals.refresh_from_payload(p)
        self.assertEqual(saved["signals"]["crowded_longs_smart_money_fading"], [])
        self.assertEqual(saved["signals"]["crowded_shorts_smart_money_buying"], ["CMG"])

    def test_inspection_helpers_read_nested_signals(self):
        liquid_signals.refresh_from_payload(self.payload())
        self.assertEqual(liquid_signals.crowded_longs(), ["GPN"])
        self.assertEqual(liquid_signals.crowded_shorts(), ["CMG"])
        self.assertIn("1 crowded longs", liquid_signals.signal_note())

    def test_punctuation_only_symbol_is_rejected(self):
        p = self.payload()
        p["positioning"]["long_heavy_crowd"] = [{"symbol": "..."}]
        p["positioning"]["short_heavy_crowd"] = []
        with self.assertRaises(ValueError):
            liquid_signals.refresh_from_payload(p)

    def test_failed_refresh_preserves_existing_cache(self):
        liquid_signals.refresh_from_payload(self.payload())
        before = self.cache.read_text(encoding="utf-8")
        bad = self.payload()
        bad["positioning"] = {"long_heavy_crowd": [], "short_heavy_crowd": []}
        with self.assertRaises(ValueError):
            liquid_signals.refresh_from_payload(bad)
        self.assertEqual(self.cache.read_text(encoding="utf-8"), before)

    def test_refresh_from_mcp_still_works(self):
        doc = liquid_signals.refresh_from_mcp(
            {"items": []}, {"long_heavy_crowd": [{"symbol": "ABC"}]}, [], "paper")
        self.assertEqual(doc["signals"]["crowded_longs_smart_money_fading"], ["ABC"])

    def test_cli_from_json_writes_cache(self):
        path = Path(self.t.name) / "payload.json"
        path.write_text(json.dumps(self.payload()), encoding="utf-8")
        rc = liquid_signals.main(["--from-json", str(path), "--account", "paper"])
        self.assertEqual(rc, 0)
        self.assertTrue(self.cache.exists())

    def test_cli_from_json_fails_closed_on_missing_file(self):
        rc = liquid_signals.main(["--from-json", str(Path(self.t.name) / "nope.json")])
        self.assertEqual(rc, 2)
        self.assertFalse(self.cache.exists())


if __name__ == "__main__":
    unittest.main()
