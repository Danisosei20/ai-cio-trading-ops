"""The broker-submission choke point enforces authorization when enabled."""
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from robinhood_tools import paper_flow

KEY = "broker-boundary-key"


class PaperFlowGatewayTests(unittest.TestCase):
    def test_buy_without_authorization_rejected_when_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, EXECUTION_AUTHORIZATION_ENABLED="true",
                       EXECUTION_AUTH_HMAC_KEY=KEY)
            with mock.patch.dict(os.environ, env, clear=True):
                ok, reason = paper_flow.require_order_authorization(
                    paper_flow.SignalOrder(symbol="AAPL", side="buy",
                                           quantity=Decimal("1"),
                                           limit_price=Decimal("100")),
                    Path(td))
        self.assertFalse(ok)
        self.assertIn("authorization", reason.lower())

    def test_sell_exit_does_not_need_entry_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, EXECUTION_AUTHORIZATION_ENABLED="true",
                       EXECUTION_AUTH_HMAC_KEY=KEY)
            with mock.patch.dict(os.environ, env, clear=True):
                ok, reason = paper_flow.require_order_authorization(
                    paper_flow.SignalOrder(symbol="AAPL", side="sell",
                                           quantity=Decimal("1"),
                                           limit_price=Decimal("100")),
                    Path(td))
        self.assertTrue(ok, reason)

    def test_flag_off_allows_legacy_path(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {}, clear=True):
                ok, reason = paper_flow.require_order_authorization(
                    paper_flow.SignalOrder(symbol="AAPL", side="buy",
                                           quantity=Decimal("1"),
                                           limit_price=Decimal("100")),
                    Path(td))
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
