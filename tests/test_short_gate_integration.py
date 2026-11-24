"""Short entries are rejected before any broker order when eligibility is unknown."""
import unittest
from unittest import mock

from scripts import daytrade_engine


class _Transport:
    def __init__(self, account, asset):
        self.account = account
        self.asset = asset

    def request(self, method, path, payload=None):
        if path.startswith("/v2/assets"):
            return self.asset
        if path == "/v2/account":
            return self.account
        raise AssertionError(f"unexpected path {path}")


class ShortGateIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.account = {"id": "acct", "shorting_enabled": True, "status": "ACTIVE",
                        "trading_blocked": False, "account_blocked": False,
                        "trade_suspended_by_user": False}
        self.asset = {"symbol": "AAPL", "status": "active", "tradable": True,
                      "shortable": True, "borrow_status": "easy_to_borrow"}

    def test_short_plan_with_verified_eligibility_passes(self):
        ok, reason = daytrade_engine.require_short_eligibility(
            {"ticker": "AAPL", "direction": "short"},
            _Transport(self.account, self.asset))
        self.assertTrue(ok, reason)

    def test_short_plan_with_unverifiable_eligibility_is_blocked(self):
        broken = mock.Mock()
        broken.request.side_effect = RuntimeError("broker unreachable")
        ok, reason = daytrade_engine.require_short_eligibility(
            {"ticker": "AAPL", "direction": "short"}, broken)
        self.assertFalse(ok)
        self.assertIn("broker", reason.lower())

    def test_long_plan_does_not_require_short_check(self):
        ok, reason = daytrade_engine.require_short_eligibility(
            {"ticker": "AAPL", "direction": "long"}, mock.Mock())
        self.assertTrue(ok)

    def test_place_order_calls_short_gate_for_short_entries(self):
        src = open(daytrade_engine.__file__, encoding="utf-8").read()
        self.assertIn("require_short_eligibility(plan, backend.transport)", src)


if __name__ == "__main__":
    unittest.main()
