import json
import tempfile
import unittest
from pathlib import Path

from scripts.news_daily import news_readout, select_movers


class NewsDailyTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def _bars(self, symbol, closes, run="r1"):
        d = self.root / "outputs/orchestrator" / run / f"bars_{symbol}.json"
        d.parent.mkdir(parents=True, exist_ok=True)
        bars = [{"ts": f"2026-11-{16+i:02d}T09:30:00-05:00", "open": c,
                 "high": c, "low": c, "close": c, "volume": 100}
                for i, c in enumerate(closes)]
        d.write_text(json.dumps({"symbol": symbol, "bars": bars,
                                 "generated_at": "2026-11-22T10:00:00-05:00"}),
                     encoding="utf-8")

    def test_unavailable_when_no_bars(self):
        self.assertEqual(select_movers(self.root), [])

    def test_ranks_by_absolute_move(self):
        self._bars("A", [100, 80])      # -20%
        self._bars("B", [100, 95])      # -5%
        self._bars("C", [100, 110])     # +10%
        picked = select_movers(self.root, top=3)
        self.assertEqual([s for s, _ in picked], ["A", "C", "B"])

    def test_top_limits_selection(self):
        self._bars("A", [100, 80])
        self._bars("B", [100, 90])
        self.assertEqual(len(select_movers(self.root, top=1)), 1)

    def test_readout_persists_artifact_and_passes_selected_symbols(self):
        self._bars("A", [100, 80])
        self._bars("B", [100, 95])
        seen = {}
        doc = news_readout(self.root, top=2,
                           scout=lambda syms: {"tickers": [{"ticker": s,
                                    "sources": [{"url": "https://x.test/1",
                                                 "title": "t"}]} for s in syms]},
                           explain=lambda syms, docs: (
                               seen.update(syms=syms, docs=docs) or
                               {"status": "ok", "rows": []}))
        self.assertEqual(doc["status"], "ok")
        self.assertEqual(seen["syms"], ["A", "B"])
        self.assertEqual(len(seen["docs"]), 2)
        self.assertEqual(len(doc["selection"]), 2)
        artifacts = sorted((self.root / "outputs/news/explanations").glob("*.json"))
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(json.loads(artifacts[0].read_text(encoding="utf-8"))["status"], "ok")

    def test_no_bars_returns_unavailable_without_calling_dependencies(self):
        def fail(*a, **k):
            raise AssertionError("should not be called")
        doc = news_readout(self.root, scout=fail, explain=fail)
        self.assertEqual(doc["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
