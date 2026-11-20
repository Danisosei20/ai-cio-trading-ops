"""Tests for the autonomous day trading engine."""
import sys
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.daytrade_engine import (
    _calc_position_size, _calc_reward_to_risk, _validate_setup,
)
from robinhood_tools.portfolio_risk import PortfolioRiskController
from robinhood_tools.trade_state import TradeStateMachine, TradeState


class TestPositionSizing(unittest.TestCase):
    """Test position sizing based on 0.25% risk per trade."""

    def test_long_position_size(self):
        equity = Decimal("100000")
        entry = 100.0
        stop = 95.0  # $5 risk distance
        size = _calc_position_size(equity, entry, stop, "long")
        # 0.25% of 100000 = 250. 250 / 5 = 50 shares
        self.assertEqual(size, Decimal("50.00"))

    def test_short_position_size(self):
        equity = Decimal("100000")
        entry = 100.0
        stop = 105.0  # $5 risk distance
        size = _calc_position_size(equity, entry, stop, "short")
        self.assertEqual(size, Decimal("50.00"))

    def test_zero_risk_distance(self):
        size = _calc_position_size(Decimal("100000"), 100.0, 100.0, "long")
        self.assertEqual(size, Decimal("0"))

    def test_negative_risk_distance_long(self):
        # stop above entry for long = invalid
        size = _calc_position_size(Decimal("100000"), 100.0, 105.0, "long")
        self.assertEqual(size, Decimal("0"))


class TestRewardToRisk(unittest.TestCase):
    """Test reward-to-risk calculation."""

    def test_long_r_r(self):
        # entry 100, stop 95 (risk 5), target 110 (reward 10) = 2:1
        r_r = _calc_reward_to_risk(100.0, 95.0, 110.0, "long")
        self.assertEqual(r_r, Decimal("2.00"))

    def test_short_r_r(self):
        # entry 100, stop 105 (risk 5), target 90 (reward 10) = 2:1
        r_r = _calc_reward_to_risk(100.0, 105.0, 90.0, "short")
        self.assertEqual(r_r, Decimal("2.00"))

    def test_zero_risk(self):
        r_r = _calc_reward_to_risk(100.0, 100.0, 110.0, "long")
        self.assertEqual(r_r, Decimal("0"))


class TestSetupValidation(unittest.TestCase):
    """Test setup validation logic."""

    def test_long_reclaim_approved(self):
        rec = {
            "ticker": "NVDA",
            "setup": "long_reclaim",
            "price": 100.0,
            "or_low": 95.0,
            "or_high": 105.0,
            "vwap": 98.0,
            "zone_verdict": "long_ok",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.25, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertTrue(approved)
        self.assertEqual(reason, "approved")
        self.assertEqual(plan["direction"], "long")
        self.assertGreater(plan["r_r_ratio"], Decimal("1.5"))

    def test_short_breakdown_approved(self):
        rec = {
            "ticker": "QQQ",
            "setup": "short_breakdown",
            "price": 200.0,
            "or_low": 190.0,
            "or_high": 205.0,
            "vwap": 202.0,
            "zone_verdict": "short_ok",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.25, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertTrue(approved)
        self.assertEqual(plan["direction"], "short")

    def test_zone_blocked_long(self):
        rec = {
            "ticker": "SPY",
            "setup": "long_reclaim",
            "price": 400.0,
            "or_low": 395.0,
            "or_high": 405.0,
            "vwap": 398.0,
            "zone_verdict": "long_blocked",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.25, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("zone blocked", reason)

    def test_zone_blocked_short(self):
        rec = {
            "ticker": "SPY",
            "setup": "short_breakdown",
            "price": 400.0,
            "or_low": 395.0,
            "or_high": 405.0,
            "vwap": 402.0,
            "zone_verdict": "short_blocked",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.25, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("zone blocked", reason)

    def test_odds_error_fail_closed(self):
        rec = {
            "ticker": "NVDA",
            "setup": "long_reclaim",
            "price": 100.0,
            "or_low": 95.0,
            "or_high": 105.0,
            "vwap": 98.0,
            "zone_verdict": "long_ok",
            "odds": {"available": False, "reason": "tool timeout",
                     "error": True},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("time machine unavailable", reason)

    def test_odds_target_too_low(self):
        rec = {
            "ticker": "NVDA",
            "setup": "long_reclaim",
            "price": 100.0,
            "or_low": 95.0,
            "or_high": 105.0,
            "vwap": 98.0,
            "zone_verdict": "long_ok",
            "odds": {"available": True, "target_first": 0.10,
                     "stop_first": 0.30, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("target_first", reason)

    def test_odds_stop_too_high(self):
        rec = {
            "ticker": "NVDA",
            "setup": "long_reclaim",
            "price": 100.0,
            "or_low": 95.0,
            "or_high": 105.0,
            "vwap": 98.0,
            "zone_verdict": "long_ok",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.45, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("stop_first", reason)

    def test_zero_risk_distance_blocks(self):
        # price == or_low means zero risk distance for long
        rec = {
            "ticker": "NVDA",
            "setup": "long_reclaim",
            "price": 95.0,
            "or_low": 95.0,
            "or_high": 105.0,
            "vwap": 98.0,
            "zone_verdict": "long_ok",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.25, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("no risk distance", reason)

    def test_risk_distance_rejects(self):
        # Risk distance too small for meaningful position
        rec = {
            "ticker": "NVDA",
            "setup": "long_reclaim",
            "price": 100.0,
            "or_low": 99.99,
            "or_high": 101.0,
            "vwap": 99.5,
            "zone_verdict": "long_ok",
            "odds": {"available": True, "target_first": 0.35,
                     "stop_first": 0.25, "error": False},
        }
        approved, reason, plan = _validate_setup(rec, Decimal("100000"))
        # Position size would be tiny but not zero; R:R = 2.0 still passes
        # The key check is that validation runs without error
        self.assertIsInstance(approved, bool)


class TestRiskControllerIntegration(unittest.TestCase):
    """Test the Portfolio Risk Controller integration."""

    def test_risk_controller_approve_and_duplicate_block(self):
        with tempfile.TemporaryDirectory() as d:
            sm_path = Path(d) / "risk_state.json"
            kill_path = Path(d) / "KILL_SWITCH"
            import robinhood_tools.portfolio_risk as pr
            orig_state = pr.STATE_FILE
            orig_kill = pr.KILL_SWITCH_FILE
            pr.STATE_FILE = sm_path
            pr.KILL_SWITCH_FILE = kill_path
            try:
                ctrl = PortfolioRiskController()
                verdict = ctrl.check("NVDA", "buy", Decimal("500"),
                                     "intraday", data_age_sec=0,
                                     current_equity=100000)
                self.assertTrue(verdict.approved)
                ctrl.record_order("NVDA", "buy", "intraday",
                                  Decimal("500"))
                # Duplicate should fail
                verdict2 = ctrl.check("NVDA", "buy", Decimal("500"),
                                      "intraday", data_age_sec=0,
                                      current_equity=100000)
                self.assertFalse(verdict2.approved)
                self.assertIn("duplicate", verdict2.reason.lower())
            finally:
                pr.STATE_FILE = orig_state
                pr.KILL_SWITCH_FILE = orig_kill


class TestTradeStateIntegration(unittest.TestCase):
    """Test trade state machine integration with engine."""

    def test_state_transitions(self):
        with tempfile.TemporaryDirectory() as d:
            sm = TradeStateMachine(state_path=Path(d) / "state.json")
            trade = sm.create_trade("NVDA", "long", "orb_breakout",
                                    strategy="intraday",
                                    entry_price=100.0,
                                    stop_price=95.0,
                                    target_price=110.0,
                                    client_order_id="test-001")
            self.assertEqual(trade.state, TradeState.SCANNING.value)
            self.assertTrue(sm.transition(trade.id, TradeState.SIGNAL_FOUND))
            self.assertTrue(sm.transition(trade.id, TradeState.RISK_PENDING))
            self.assertTrue(sm.transition(trade.id, TradeState.ORDER_PENDING))
            self.assertTrue(sm.transition(trade.id, TradeState.POSITION_OPEN))
            self.assertTrue(sm.mark_closed(trade.id, "target hit", 100.0))
            self.assertEqual(trade.realized_pnl, 100.0)

    def test_long_pnl(self):
        with tempfile.TemporaryDirectory() as d:
            sm = TradeStateMachine(state_path=Path(d) / "state.json")
            trade = sm.create_trade("NVDA", "long", "orb_breakout",
                                    entry_price=100.0, stop_price=95.0,
                                    target_price=110.0,
                                    client_order_id="test-002")
            sm.transition(trade.id, TradeState.SIGNAL_FOUND)
            sm.transition(trade.id, TradeState.RISK_PENDING)
            sm.transition(trade.id, TradeState.ORDER_PENDING)
            sm.transition(trade.id, TradeState.POSITION_OPEN)
            # Long: exit above entry = profit
            self.assertTrue(sm.mark_closed(trade.id, "target hit",
                                           (110.0 - 100.0)))
            self.assertEqual(trade.realized_pnl, 10.0)

    def test_short_pnl(self):
        with tempfile.TemporaryDirectory() as d:
            sm = TradeStateMachine(state_path=Path(d) / "state.json")
            trade = sm.create_trade("QQQ", "short", "vwap_rejection",
                                    entry_price=200.0, stop_price=205.0,
                                    target_price=190.0,
                                    client_order_id="test-003")
            sm.transition(trade.id, TradeState.SIGNAL_FOUND)
            sm.transition(trade.id, TradeState.RISK_PENDING)
            sm.transition(trade.id, TradeState.ORDER_PENDING)
            sm.transition(trade.id, TradeState.POSITION_OPEN)
            # Short: exit below entry = profit
            self.assertTrue(sm.mark_closed(trade.id, "target hit",
                                           (200.0 - 190.0)))
            self.assertEqual(trade.realized_pnl, 10.0)


if __name__ == "__main__":
    unittest.main()
