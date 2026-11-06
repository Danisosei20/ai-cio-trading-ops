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

# The new test bodies call the bear-book helpers directly; bind them to the
# loaded module so the loader above stays the single import path.
rank_weak = SCR.rank_weak
screener_payload = SCR.screener_payload


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

    def test_rank_weak_orders_by_lowest_mom20(self):
        rows = [{"symbol": "A", "mom20": 0.30, "relvol": 1.0, "dist52w": -0.1},
                {"symbol": "B", "mom20": -0.20, "relvol": 1.0, "dist52w": -0.1},
                {"symbol": "C", "mom20": 0.05, "relvol": 1.0, "dist52w": -0.1}]
        weak = rank_weak(rows, 2)
        self.assertEqual([r["symbol"] for r in weak], ["B", "C"])
        self.assertIn("score", weak[0])          # scorer still applied

    def test_rank_weak_single_row_does_not_crash(self):
        rows = [{"symbol": "A", "mom20": 0.1, "relvol": 1.0, "dist52w": 0.0}]
        self.assertEqual(len(rank_weak(rows, 5)), 1)

    def test_rank_weak_scores_match_rank(self):
        # One symbol, one score, whichever book reads it (Task 5 reads "score"
        # out of weak.json). rank_weak delegates scoring to rank; this pins the
        # contract from the callers' side, so a second copy of the weights
        # re-introduced here fails instead of silently disagreeing.
        closes, volumes = uptrend()
        rows = []
        for i, mom in enumerate([0.05, 0.30, 0.12, -0.08]):
            feat = dict(SCR.features(closes, volumes))
            feat["symbol"] = f"T{i}"
            feat["mom20"] = mom
            rows.append(feat)
        weak = rank_weak(rows, len(rows))
        self.assertEqual({r["symbol"]: r["score"] for r in weak},
                         {r["symbol"]: r["score"] for r in SCR.rank(rows, len(rows))})
        # the documented 0.5/0.3/0.2 formula on real inputs, not just "a
        # score key exists": relvol and dist52w are constant across these
        # rows, so their z-scores are 0 and the score is 0.5*z(mom20).
        self.assertEqual([r["symbol"] for r in weak], ["T3", "T0", "T2", "T1"])
        self.assertEqual([r["score"] for r in weak], [-0.647, -0.173, 0.082, 0.738])

    def test_screener_payload_weak_key_is_opt_in(self):
        # pins the one constraint that matters most: latest.json keeps exactly
        # today's four keys, and only weak.json grows a "weak" tail.
        rows = [{"symbol": "A", "mom20": 0.1}]
        ranked = [{"symbol": "A", "mom20": 0.1, "score": 1.0}]
        weak = [{"symbol": "B", "mom20": -0.2, "score": -1.0}]
        long_only = screener_payload(rows, ranked, 506)
        self.assertEqual(sorted(long_only), ["date", "ranked", "screened", "universe"])
        self.assertEqual(long_only["screened"], 1)
        self.assertEqual(long_only["universe"], 506)
        self.assertEqual(long_only["ranked"], ranked)
        bear = screener_payload(rows, ranked, 506, weak=weak)
        self.assertEqual(sorted(bear),
                         ["date", "ranked", "screened", "universe", "weak"])
        self.assertEqual(bear["weak"], weak)


if __name__ == "__main__":
    unittest.main()
