"""A close/reduce label must be backed by verified owned exposure."""
import decimal
import unittest

from robinhood_tools import paper_flow


class CloseSafetyTests(unittest.TestCase):
    def order(self, side, qty, symbol="AAPL", effect="close"):
        return paper_flow.SignalOrder(symbol=symbol, side=side,
                                      quantity=decimal.Decimal(str(qty)),
                                      limit_price=decimal.Decimal("100"),
                                      position_effect=effect)

    def test_valid_long_close(self):
        ok, why = paper_flow.validate_close_order(
            self.order("sell", 5), {"AAPL": {"qty": 10, "direction": "long",
                                              "instrument": "AAPL"}})
        self.assertTrue(ok, why)

    def test_oversized_long_close_rejected(self):
        ok, why = paper_flow.validate_close_order(
            self.order("sell", 15), {"AAPL": {"qty": 10, "direction": "long",
                                               "instrument": "AAPL"}})
        self.assertFalse(ok)

    def test_wrong_direction_close_rejected(self):
        ok, why = paper_flow.validate_close_order(
            self.order("sell", 5), {"AAPL": {"qty": 10, "direction": "short",
                                              "instrument": "AAPL"}})
        self.assertFalse(ok)

    def test_valid_short_cover(self):
        ok, why = paper_flow.validate_close_order(
            self.order("buy", 5, symbol="TSLA"), {"TSLA": {"qty": 10, "direction": "short",
                                             "instrument": "TSLA"}})
        self.assertTrue(ok, why)

    def test_wrong_instrument_rejected(self):
        ok, why = paper_flow.validate_close_order(
            self.order("sell", 5, symbol="MSFT"),
            {"AAPL": {"qty": 10, "direction": "long", "instrument": "AAPL"}})
        self.assertFalse(ok)

    def test_pending_reservation_reduces_available(self):
        ok, why = paper_flow.validate_close_order(
            self.order("sell", 8),
            {"AAPL": {"qty": 10, "direction": "long", "instrument": "AAPL"}},
            pending={"AAPL": 5})
        self.assertFalse(ok)

    def test_broker_position_unavailable_fails_closed(self):
        ok, why = paper_flow.validate_close_order(self.order("sell", 5), None)
        self.assertFalse(ok)

    def test_unknown_effect_does_not_bypass(self):
        ok, why = paper_flow.validate_close_order(
            self.order("sell", 5, effect="bogus"), {})
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
