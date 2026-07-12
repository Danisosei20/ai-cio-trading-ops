from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.lifecycle import PositionObservation, evaluate_exit


class TradeLifecycleTests(unittest.TestCase):
    def test_one_active_task_per_symbol_and_full_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            row = db.begin_trade_lifecycle("aapl", buy_approval_id="buy-1")
            self.assertEqual(row["task_name"], "AAPL")
            with self.assertRaises(PolicyViolation):
                db.begin_trade_lifecycle("AAPL")
            db.mark_position_open("AAPL", order_id="order-buy")
            db.mark_sell_pending("AAPL", approval_id="sell-1")
            row = db.close_trade_lifecycle("AAPL", order_id="order-sell", realized_profit="42.15")
            self.assertEqual(row["status"], "closed")
            self.assertEqual(row["realized_profit"], "42.15")

    def test_profit_alone_does_not_sell(self):
        observation = PositionObservation(
            symbol="AAPL", quantity=Decimal("2"), average_cost=Decimal("100"),
            current_price=Decimal("125"), peak_price=Decimal("126"), holding_days=60,
            thesis_status="unchanged", valuation_status="fair", portfolio_weight=Decimal("0.05"),
        )
        self.assertEqual(evaluate_exit(observation, target_return=Decimal("0.20")).action, "hold")

    def test_target_plus_trailing_reversal_opens_trim_review(self):
        observation = PositionObservation(
            symbol="AAPL", quantity=Decimal("2"), average_cost=Decimal("100"),
            current_price=Decimal("120"), peak_price=Decimal("132"), holding_days=60,
            thesis_status="unchanged", valuation_status="stretched", portfolio_weight=Decimal("0.05"),
        )
        decision = evaluate_exit(observation, target_return=Decimal("0.15"))
        self.assertEqual(decision.action, "review_trim")
        self.assertTrue(decision.requires_codex_approval)

    def test_multi_day_paper_lifecycle_keeps_one_task_through_sale(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "paper.db")
            db.begin_trade_lifecycle("MSFT", buy_approval_id="paper-buy")
            db.mark_position_open("MSFT", order_id="paper-buy-order")
            self.assertEqual(db.get_trade_lifecycle("MSFT")["task_name"], "MSFT")
            db.mark_sell_pending("MSFT", approval_id="paper-sell")
            closed = db.close_trade_lifecycle("MSFT", order_id="paper-sell-order", realized_profit="4.25")
            self.assertEqual((closed["status"], closed["realized_profit"]), ("closed", "4.25"))


if __name__ == "__main__":
    unittest.main()
