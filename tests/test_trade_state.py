"""Tests for the trade state machine."""
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robinhood_tools.trade_state import TradeStateMachine, TradeState


class TestTradeState(unittest.TestCase):

    def test_create_trade(self):
        with tempfile.TemporaryDirectory() as d:
            sm = TradeStateMachine(state_path=Path(d) / "state.json")
            trade = sm.create_trade("NVDA", "long", "orb_breakout", strategy="intraday")
            self.assertEqual(trade.state, TradeState.SCANNING.value)
            self.assertEqual(trade.ticker, "NVDA")
            self.assertEqual(len(sm.trades), 1)

    def test_valid_transition(self):
        with tempfile.TemporaryDirectory() as d:
            sm = TradeStateMachine(state_path=Path(d) / "state.json")
            trade = sm.create_trade("NVDA", "long", "orb_breakout")
            self.assertTrue(sm.transition(trade.id, TradeState.SIGNAL_FOUND))
            self.assertEqual(sm.trades[trade.id].state, TradeState.SIGNAL_FOUND.value)
            self.assertTrue(sm.transition(trade.id, TradeState.RISK_PENDING))
            self.assertTrue(sm.transition(trade.id, TradeState.ORDER_PENDING))
            self.assertTrue(sm.transition(trade.id, TradeState.POSITION_OPEN))

    def test_invalid_transition(self):
        with tempfile.TemporaryDirectory() as d:
            sm = TradeStateMachine(state_path=Path(d) / "state.json")
            trade = sm.create_trade("NVDA", "long", "orb_breakout")
            self.assertFalse(sm.transition(trade.id, TradeState.POSITION_OPEN))
            self.assertEqual(sm.trades[trade.id].state, TradeState.SCANNING.value)

    def test_terminal_states(self):
        with tempfile.TemporaryDirectory() as d:
            sm = TradeStateMachine(state_path=Path(d) / "state.json")
            trade = sm.create_trade("NVDA", "long", "orb_breakout")
            sm.transition(trade.id, TradeState.SIGNAL_FOUND)
            sm.transition(trade.id, TradeState.RISK_PENDING)
            sm.transition(trade.id, TradeState.ORDER_PENDING)
            sm.transition(trade.id, TradeState.POSITION_OPEN)
            self.assertTrue(sm.transition(trade.id, TradeState.CLOSED, "target hit"))
            self.assertFalse(sm.transition(trade.id, TradeState.POSITION_OPEN))

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            sm = TradeStateMachine(state_path=path)
            trade = sm.create_trade("AAPL", "short", "vwap_rejection")
            sm.transition(trade.id, TradeState.SIGNAL_FOUND)
            sm2 = TradeStateMachine(state_path=path)
            self.assertEqual(len(sm2.trades), 1)
            self.assertEqual(sm2.trades[trade.id].state, TradeState.SIGNAL_FOUND.value)

    def test_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            sm = TradeStateMachine(state_path=path)
            trade = sm.create_trade("NVDA", "long", "orb_breakout")
            sm.transition(trade.id, TradeState.SIGNAL_FOUND)
            sm.transition(trade.id, TradeState.RISK_PENDING)
            sm.transition(trade.id, TradeState.ORDER_PENDING)
            actions = sm.recover_from_broker([{"symbol": "NVDA", "qty": "10"}], [])
            self.assertEqual(actions["matched"], 1)
            self.assertEqual(sm.trades[trade.id].state, TradeState.POSITION_OPEN.value)
            actions2 = sm.recover_from_broker([{"symbol": "TSLA", "qty": "5"}], [])
            self.assertIn("TSLA", actions2["orphaned_positions"])

    def test_idempotency(self):
        with tempfile.TemporaryDirectory() as d:
            sm = TradeStateMachine(state_path=Path(d) / "state.json")
            sm.create_trade("NVDA", "long", "orb_breakout",
                                    client_order_id="NVDA-2026-11-04-001")
            self.assertTrue(sm.check_idempotency("NVDA-2026-11-04-001"))
            self.assertFalse(sm.check_idempotency("NONEXISTENT"))

    def test_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            sm = TradeStateMachine(state_path=Path(d) / "state.json")
            trade = sm.create_trade("NVDA", "long", "orb_breakout")
            sm.transition(trade.id, TradeState.SIGNAL_FOUND)
            sm.transition(trade.id, TradeState.RISK_PENDING)
            sm.transition(trade.id, TradeState.ORDER_PENDING)
            sm.transition(trade.id, TradeState.POSITION_OPEN)
            sm.mark_closed(trade.id, "target hit", 100.0)
            self.assertEqual(sm.trades[trade.id].state, TradeState.CLOSED.value)
            n = sm.cleanup_old(0)
            self.assertEqual(n, 1)
            self.assertEqual(len(sm.trades), 0)

    def test_summary(self):
        with tempfile.TemporaryDirectory() as d:
            sm = TradeStateMachine(state_path=Path(d) / "state.json")
            t1 = sm.create_trade("NVDA", "long", "orb_breakout")
            sm.transition(t1.id, TradeState.SIGNAL_FOUND)
            sm.transition(t1.id, TradeState.RISK_PENDING)
            sm.transition(t1.id, TradeState.ORDER_PENDING)
            sm.transition(t1.id, TradeState.POSITION_OPEN)
            sm.mark_closed(t1.id, "target", 50.0)
            sm.create_trade("TSLA", "short", "vwap_rejection")
            s = sm.summary()
            self.assertEqual(s["total_trades"], 2)
            self.assertEqual(s["open"], 1)
            self.assertEqual(s["closed"], 1)
            self.assertEqual(s["total_realized_pnl"], 50.0)

    def test_reconciliation_required(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            sm = TradeStateMachine(state_path=path)
            trade = sm.create_trade("NVDA", "long", "orb_breakout")
            sm.transition(trade.id, TradeState.SIGNAL_FOUND)
            sm.transition(trade.id, TradeState.RISK_PENDING)
            sm.transition(trade.id, TradeState.ORDER_PENDING)
            actions = sm.recover_from_broker([], [])
            self.assertIn("NVDA", actions["missing_positions"])
            self.assertEqual(sm.trades[trade.id].state, TradeState.RECONCILIATION_REQUIRED.value)
            self.assertTrue(sm.transition(trade.id, TradeState.CLOSED, "reconciled"))


if __name__ == "__main__":
    unittest.main()
