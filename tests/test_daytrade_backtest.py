"""Tests for the intraday backtesting engine."""
import sys
import unittest
from pathlib import Path
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.daytrade_backtest import (
    _compute_ema, _compute_vwap, _is_regime_risk_on,
    _simulate_trade, _aggregate_results,
)


class TestEMA(unittest.TestCase):
    """Test EMA computation."""

    def test_ema_basic(self):
        closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]
        ema = _compute_ema(closes, 5)
        # First 4 should be NaN
        self.assertTrue(all(e != e for e in ema[:4]))  # NaN check
        self.assertEqual(len(ema), 10)
        # Last EMA should be close to recent prices
        self.assertGreater(ema[-1], 14.0)
        self.assertLess(ema[-1], 19.0)

    def test_ema_insufficient_data(self):
        closes = [10.0, 11.0, 12.0]
        ema = _compute_ema(closes, 5)
        self.assertTrue(all(e != e for e in ema))  # all NaN


class TestVWAP(unittest.TestCase):
    """Test VWAP computation."""

    def test_vwap_basic(self):
        bars = [
            {"high": 11, "low": 9, "close": 10, "volume": 100},
            {"high": 13, "low": 11, "close": 12, "volume": 200},
            {"high": 15, "low": 13, "close": 14, "volume": 300},
        ]
        vwap = _compute_vwap(bars)
        self.assertEqual(len(vwap), 3)
        # First VWAP = typical price = (11+9+10)/3 = 10
        self.assertAlmostEqual(vwap[0], 10.0, places=4)
        # Last VWAP should be weighted toward later prices
        self.assertGreater(vwap[-1], vwap[0])


class TestRegime(unittest.TestCase):
    """Test market regime detection."""

    def test_risk_on(self):
        # Close above 50d SMA
        closes = [100.0] * 50 + [110.0]
        regime = _is_regime_risk_on(closes, 50)
        self.assertEqual(regime, "RISK_ON")

    def test_chop(self):
        closes = [100.0] * 50 + [90.0]
        regime = _is_regime_risk_on(closes, 50)
        self.assertEqual(regime, "CHOP")

    def test_insufficient_data(self):
        closes = [100.0] * 10
        regime = _is_regime_risk_on(closes, 50)
        self.assertEqual(regime, "UNKNOWN")


class TestTradeSimulation(unittest.TestCase):
    """Test individual trade simulation."""

    def test_long_profitable(self):
        # Create bars where price goes up after entry
        bars = []
        base = 100.0
        for i in range(20):
            bars.append({
                "open": base + i,
                "high": base + i + 2,
                "low": base + i - 1,
                "close": base + i + 1,
                "volume": 1000,
            })
        result = _simulate_trade(bars, 6, "long", "test", Decimal("0.005"),
                                 Decimal("5"))
        self.assertIsNotNone(result)
        self.assertEqual(result["direction"], "long")
        # entry at bar 6: close = 107, OR low = 99, risk = 8
        # qty = int(250 / 8) = 31
        self.assertEqual(result["qty"], 31)

    def test_short_profitable(self):
        # Create bars where price goes down after entry
        bars = []
        base = 100.0
        for i in range(20):
            bars.append({
                "open": base - i,
                "high": base - i + 1,
                "low": base - i - 2,
                "close": base - i - 1,
                "volume": 1000,
            })
        result = _simulate_trade(bars, 6, "short", "test", Decimal("0.005"),
                                 Decimal("5"))
        self.assertIsNotNone(result)
        self.assertEqual(result["direction"], "short")

    def test_insufficient_bars(self):
        bars = [
            {"open": 100, "high": 102, "low": 98, "close": 100, "volume": 1000}
        ] * 3
        result = _simulate_trade(bars, 2, "long", "test", Decimal("0.005"),
                                 Decimal("5"))
        self.assertIsNone(result)


class TestAggregation(unittest.TestCase):
    """Test result aggregation."""

    def test_aggregate_empty(self):
        result = _aggregate_results("TEST", [])
        self.assertEqual(result["trades"], 0)

    def test_aggregate_with_trades(self):
        trades = [
            {"direction": "long", "net_pnl": 100.0, "commission": 1.0,
             "borrow_fee": 0.0, "return_pct": 0.01, "r_r": 2.0,
             "entry_price": 100.0, "exit_price": 110.0},
            {"direction": "short", "net_pnl": -50.0, "commission": 1.0,
             "borrow_fee": 0.5, "return_pct": -0.005, "r_r": 1.0,
             "entry_price": 200.0, "exit_price": 205.0},
            {"direction": "long", "net_pnl": 75.0, "commission": 1.0,
             "borrow_fee": 0.0, "return_pct": 0.0075, "r_r": 1.5,
             "entry_price": 50.0, "exit_price": 55.0},
        ]
        result = _aggregate_results("TEST", trades)
        self.assertEqual(result["trades"], 3)
        self.assertEqual(result["longs"], 2)
        self.assertEqual(result["shorts"], 1)
        self.assertEqual(result["wins"], 2)
        self.assertEqual(result["losses"], 1)
        self.assertAlmostEqual(result["win_rate"], 2/3, places=4)
        self.assertAlmostEqual(result["total_pnl"], 125.0, places=2)


if __name__ == "__main__":
    unittest.main()
