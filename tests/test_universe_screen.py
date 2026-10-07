"""Universe screen scoring tests (pure functions only, no network)."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def load_module():
    spec = importlib.util.spec_from_file_location(
        "universe_screen", Path("scripts/universe_screen.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCR = load_module()


def uptrend(n: int = 100, start: float = 100.0) -> tuple[list[float], list[float]]:
    closes = [start + i * 0.8 + (i % 5) for i in range(n)]
    volumes = [2_000_000 + (i % 10) * 100_000 for i in range(n)]
    return closes, volumes


class ScreenTest(unittest.TestCase):
    def test_features_uptrend(self):
        closes, volumes = uptrend()
        feat = SCR.features(closes, volumes)
        self.assertIsNotNone(feat)
        self.assertGreater(feat["mom20"], 0)
        self.assertLess(feat["rsi"], 80)

    def test_features_rejects_thin_or_short(self):
        closes, volumes = uptrend(n=30)
        self.assertIsNone(SCR.features(closes, volumes))
        closes, volumes = uptrend()
        penny = [c / 50 for c in closes]
        self.assertIsNone(SCR.features(penny, volumes))

    def test_overbought_rejected(self):
        closes = [100.0 + i * 5 for i in range(100)]  # vertical
        volumes = [5_000_000] * 100
        self.assertIsNone(SCR.features(closes, volumes))

    def test_rank_orders_by_score(self):
        closes, volumes = uptrend()
        rows = []
        for i, mom in enumerate([0.05, 0.30, 0.12]):
            feat = dict(SCR.features(closes, volumes))
            feat["symbol"] = f"T{i}"
            feat["mom20"] = mom
            rows.append(feat)
        ranked = SCR.rank(rows, 3)
        self.assertEqual([r["symbol"] for r in ranked], ["T1", "T2", "T0"])
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])

    def test_rsi_bounds(self):
        flat = [100.0] * 100
        self.assertEqual(SCR.rsi(flat), 100.0)
        self.assertIsNone(SCR.rsi([100.0] * 10))


if __name__ == "__main__":
    unittest.main()
