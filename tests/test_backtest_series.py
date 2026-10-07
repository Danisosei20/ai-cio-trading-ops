"""Backtest series tests (no network: synthetic bars only)."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def load_module():
    spec = importlib.util.spec_from_file_location(
        "strategy_backtest", Path("scripts/strategy_backtest.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BT = load_module()


def bars(n: int = 100, start: float = 100.0, drift: float = 0.5) -> list[dict]:
    out = []
    price = start
    for i in range(n):
        price += drift
        out.append({"date": f"2026-01-{(i % 28) + 1:02d}", "open": price - 0.2,
                    "high": price + 0.3, "low": price - 0.4, "close": price,
                    "volume": 1000000})
    return out


class SeriesTest(unittest.TestCase):
    def test_curve_aligns_with_bars(self):
        data = bars()
        pos = BT.signals({"family": "sma_cross", "fast": 10, "slow": 20}, data)
        result = BT.run_segment(data, pos, 10000.0, 0.005, 0.0005)
        self.assertEqual(len(result["curve"]), len(data))
        self.assertEqual(result["curve"][0]["date"], data[0]["date"])
        self.assertGreater(result["return"], 0)  # steady uptrend
        self.assertIn("buy_hold", result)

    def test_grid_labels(self):
        variants = BT.grid(False)
        self.assertTrue(all(BT.label(v) for v in variants))
        self.assertGreater(len(BT.grid(True)), len(variants))

    def test_overnight_gap_capture(self):
        # steady overnight gaps up, flat intraday: strategy must profit
        data = []
        price = 100.0
        for i in range(80):
            data.append({"date": f"2026-01-{(i % 28) + 1:02d}", "open": price,
                         "high": price + 0.2, "low": price - 0.2,
                         "close": price + 0.1, "volume": 1000000})
            price += 0.5
        pos = BT.signals({"family": "overnight", "trend": 50}, data)
        self.assertTrue(any(pos))
        result = BT.run_overnight(data, pos, 10000.0, 0.0, 0.0)
        self.assertGreater(result["trades"], 5)
        self.assertGreater(result["return"], 0)

    def test_overnight_flat_no_trades_without_trend(self):
        data = []
        for i in range(80):
            data.append({"date": f"2026-01-{(i % 28) + 1:02d}", "open": 100.0,
                         "high": 100.2, "low": 99.8, "close": 100.0,
                         "volume": 1000000})
        pos = BT.signals({"family": "overnight", "trend": 50}, data)
        result = BT.run_overnight(data, pos, 10000.0, 0.005, 0.0005)
        self.assertEqual(result["return"], 0.0)


if __name__ == "__main__":
    unittest.main()
