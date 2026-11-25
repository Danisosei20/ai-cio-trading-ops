"""15:45 flatten must only touch strategy-owned exposure."""
import unittest
from decimal import Decimal

from scripts import daytrade_engine, flatten_daytrades


class OwnedTrade:
    def __init__(self, strategy):
        self.strategy = strategy


class StrategyOwnershipTests(unittest.TestCase):
    def test_overnight_trades_excluded_from_day_trade_flatten(self):
        self.assertFalse(daytrade_engine._flatten_eligible(OwnedTrade("overnight")))
        self.assertTrue(daytrade_engine._flatten_eligible(OwnedTrade("intraday")))
        self.assertFalse(daytrade_engine._flatten_eligible(OwnedTrade(None)))

    def test_flatten_requires_explicit_owned_quantity(self):
        self.assertEqual(flatten_daytrades.owned_quantity({"ticker": "AAPL"}), None)
        self.assertEqual(flatten_daytrades.owned_quantity(
            {"ticker": "AAPL", "quantity": "5"}), Decimal("5"))
        self.assertEqual(flatten_daytrades.owned_quantity(
            {"ticker": "AAPL", "qty": "3"}), Decimal("3"))

    def test_flatten_rejects_non_positive_quantity(self):
        self.assertEqual(flatten_daytrades.owned_quantity(
            {"ticker": "AAPL", "quantity": "0"}), None)
        self.assertEqual(flatten_daytrades.owned_quantity(
            {"ticker": "AAPL", "quantity": "-2"}), None)

    def test_owned_quantity_is_capped_by_broker_position(self):
        self.assertEqual(flatten_daytrades.sellable_quantity(
            {"quantity": "5"}, Decimal("3")), Decimal("3"))
        self.assertEqual(flatten_daytrades.sellable_quantity(
            {"quantity": "5"}, Decimal("10")), Decimal("5"))
        self.assertEqual(flatten_daytrades.sellable_quantity(
            {"quantity": "5"}, Decimal("0")), Decimal("0"))


if __name__ == "__main__":
    unittest.main()
