from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from robinhood_tools.accounting import account_for_sale
from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.lifecycle import PositionObservation
from robinhood_tools.models import Order
from robinhood_tools.orchestrator import DailyOrchestrator, ScreenedIdea
from robinhood_tools.portfolio import TaxLot
from robinhood_tools.reconciliation import Fill, reconcile_order
from robinhood_tools.runtime import RuntimeSettings
from robinhood_tools.risk import RiskLimits
from robinhood_tools.slack_replies import parse_safe_reply, transition_for_reply


def settings(root: Path) -> RuntimeSettings:
    return RuntimeSettings("research_only", False, "C1", 120, root / "cio.db", root / "dashboard.html", RiskLimits())


class OrchestrationIntegrationTests(unittest.TestCase):
    def test_daily_review_selects_best_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = CioDatabase(root / "cio.db")
            runner = DailyOrchestrator(db, settings(root))
            ideas = [ScreenedIdea("MSFT", 91, True, "qualified"), ScreenedIdea("AAPL", 95, True, "best")]
            result = runner.run_daily("Agentic", lambda: ideas, day=date(2026, 7, 13))
            self.assertEqual((result.action, result.symbol), ("review", "AAPL"))
            with self.assertRaises(PolicyViolation):
                runner.run_daily("Agentic", lambda: ideas, day=date(2026, 7, 13))

    def test_monitor_uses_existing_symbol_lifecycle_and_releases_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = CioDatabase(root / "cio.db")
            db.begin_trade_lifecycle("AAPL", buy_approval_id="a")
            db.mark_position_open("AAPL", order_id="o")
            observation = PositionObservation(
                "AAPL", Decimal("1"), Decimal("100"), Decimal("120"), Decimal("132"), 40,
                "unchanged", "stretched", Decimal("0.05"), False,
            )
            result = DailyOrchestrator(db, settings(root)).monitor_open_positions(
                lambda symbol: observation, target_return=lambda symbol: Decimal("0.15")
            )
            self.assertEqual(result[0][1].action, "review_trim")
            self.assertEqual(db.dashboard()["active_leases"], 0)

    def test_partial_fills_are_deduplicated_and_weighted(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            order = Order("o1", "a1", "AAPL", "sell", "partially_filled")
            fills = [
                Fill("f1", Decimal("1"), Decimal("120"), Decimal("0.10"), "2026-07-13T14:00:00Z"),
                Fill("f2", Decimal("2"), Decimal("123"), Decimal("0.20"), "2026-07-13T14:01:00Z"),
            ]
            first = reconcile_order(db, order, fills)
            second = reconcile_order(db, order, fills)
            self.assertEqual((first.new_fills, second.new_fills), (2, 0))
            self.assertEqual(first.average_fill_price, Decimal("122"))
            self.assertEqual(first.fees, Decimal("0.30"))

    def test_realized_profit_uses_tax_lots_and_fees(self):
        lots = [
            TaxLot("AAPL", date.today() - timedelta(days=500), Decimal("1"), Decimal("90"), Decimal("120")),
            TaxLot("AAPL", date.today() - timedelta(days=30), Decimal("1"), Decimal("110"), Decimal("120")),
        ]
        result = account_for_sale(lots, quantity=Decimal("1.5"), fill_price=Decimal("120"), fees=Decimal("1"))
        self.assertEqual(result.realized_profit, Decimal("34"))

    def test_slack_state_machine_never_approves(self):
        self.assertEqual(transition_for_reply(parse_safe_reply("YES")).state, "awaiting_size")
        self.assertTrue(transition_for_reply(parse_safe_reply("$250")).needs_fresh_review)
        self.assertTrue(transition_for_reply(parse_safe_reply("NO")).should_reject)
        self.assertEqual(transition_for_reply(parse_safe_reply("APPROVE abc")).state, "blocked")


if __name__ == "__main__":
    unittest.main()
