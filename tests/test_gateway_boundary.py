"""Gateway boundary: entry effect drives enforcement, not order side."""
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from robinhood_tools import paper_flow

KEY = "boundary-key"


class GatewayBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ, EXECUTION_AUTH_HMAC_KEY=KEY,
                        EXECUTION_AUTHORIZATION_ENABLED="true")

    def test_short_entry_requires_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, self.env, clear=True):
                ok, reason = paper_flow.require_order_authorization(
                    paper_flow.SignalOrder(symbol="AAPL", side="sell",
                                           quantity=Decimal("1"),
                                           limit_price=Decimal("100"),
                                           position_effect="open"), Path(td))
        self.assertFalse(ok)
        self.assertIn("authorization", reason.lower())

    def test_buy_to_cover_exit_stays_exempt(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, self.env, clear=True):
                ok, reason = paper_flow.require_order_authorization(
                    paper_flow.SignalOrder(symbol="AAPL", side="buy",
                                           quantity=Decimal("1"),
                                           limit_price=Decimal("100"),
                                           position_effect="close"), Path(td))
        self.assertTrue(ok, reason)

    def test_sell_exit_stays_exempt(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, self.env, clear=True):
                ok, reason = paper_flow.require_order_authorization(
                    paper_flow.SignalOrder(symbol="AAPL", side="sell",
                                           quantity=Decimal("1"),
                                           limit_price=Decimal("100"),
                                           position_effect="close"), Path(td))
        self.assertTrue(ok, reason)

    def test_signal_order_carries_authorization_fields(self):
        order = paper_flow.SignalOrder(symbol="AAPL", side="buy",
                                       quantity=Decimal("1"),
                                       limit_price=Decimal("100"))
        for field in ("authorization_id", "decision_id", "strategy_id", "position_effect"):
            self.assertTrue(hasattr(order, field), field)


if __name__ == "__main__":
    unittest.main()
