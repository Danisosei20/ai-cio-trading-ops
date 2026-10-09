"""Weekly scorecard tests (pure scoring; no network/broker)."""
from __future__ import annotations

import unittest
from pathlib import Path


class ScorecardTest(unittest.TestCase):
    def setUp(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "weekly_scorecard", Path("scripts/weekly_scorecard.py"))
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def test_fifo_round_trips(self):
        score = self.mod.score_round_trips([
            {"symbol": "NVDA", "side": "buy", "qty": "2", "price": "237.10",
             "time": "2026-10-07T10:00:00Z"},
            {"symbol": "NVDA", "side": "sell", "qty": "2", "price": "230.31",
             "time": "2026-10-08T14:00:00Z"},
            {"symbol": "MRNA", "side": "buy", "qty": "2", "price": "191.17",
             "time": "2026-10-07T11:00:00Z"},
            {"symbol": "MRNA", "side": "sell", "qty": "2", "price": "195.53",
             "time": "2026-10-08T14:00:00Z"},
        ])
        self.assertEqual(score["trades"], 2)
        self.assertEqual(score["wins"], 1)
        self.assertEqual(score["losses"], 1)
        self.assertAlmostEqual(float(score["realized"]), -13.58 + 8.72, places=1)
        self.assertEqual(score["win_rate"], 0.5)

    def test_unmatched_sell_flagged(self):
        score = self.mod.score_round_trips([
            {"symbol": "X", "side": "sell", "qty": "1", "price": "10",
             "time": "2026-10-08T10:00:00Z"},
        ])
        self.assertEqual(score["unmatched_sells"], 1)
        self.assertEqual(score["trades"], 0)

    def test_card_format(self):
        card = self.mod.format_card(
            {"realized": "-11.68", "trades": 2, "wins": 1, "losses": 1,
             "win_rate": 0.5, "avg_win": "8.72", "avg_loss": "-20.40",
             "profit_factor": "0.43", "unmatched_sells": 0},
            {"Buy->buy_placed": 2}, "2026-10-01 to 2026-10-08")
        self.assertIn("Weekly scorecard", card)
        self.assertIn("-11.68", card)



if __name__ == "__main__":
    unittest.main()
