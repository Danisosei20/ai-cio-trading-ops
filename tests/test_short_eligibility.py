"""Short-open eligibility must be broker-verified and fail closed."""
import unittest
from datetime import datetime, timedelta, timezone

from robinhood_tools import short_eligibility as se

NOW = datetime(2026, 11, 23, 20, 20, tzinfo=timezone.utc)


def account(**overrides):
    base = {"shorting_enabled": True, "status": "ACTIVE", "trading_blocked": False,
            "account_blocked": False, "trade_suspended_by_user": False,
            "buying_power": "10000"}
    base.update(overrides)
    return base


def asset(**overrides):
    base = {"symbol": "AAPL", "status": "active", "tradable": True,
            "shortable": True, "borrow_status": "easy_to_borrow", "class": "us_equity"}
    base.update(overrides)
    return base


class ShortEligibilityTests(unittest.TestCase):
    def test_easy_to_borrow_eligible(self):
        r = se.validate_short_eligibility("AAPL", account=account(), asset=asset(), now=NOW)
        self.assertTrue(r["eligible"], r["reason"])
        self.assertEqual(r["borrow_status"], "easy_to_borrow")
        self.assertEqual(r["broker"], "alpaca")

    def test_non_shortable_rejected(self):
        r = se.validate_short_eligibility("AAPL", account=account(), asset=asset(shortable=False), now=NOW)
        self.assertFalse(r["eligible"])
        self.assertIn("shortable", r["reason"])

    def test_hard_to_borrow_rejected(self):
        r = se.validate_short_eligibility("AAPL", account=account(),
                                          asset=asset(borrow_status="hard_to_borrow"), now=NOW)
        self.assertFalse(r["eligible"])
        self.assertIn("borrow", r["reason"].lower())

    def test_missing_borrow_status_rejected(self):
        data = asset()
        data.pop("borrow_status")
        r = se.validate_short_eligibility("AAPL", account=account(), asset=data, now=NOW)
        self.assertFalse(r["eligible"])
        self.assertIn("borrow", r["reason"].lower())

    def test_account_not_permitted_rejected(self):
        r = se.validate_short_eligibility("AAPL", account=account(shorting_enabled=False),
                                          asset=asset(), now=NOW)
        self.assertFalse(r["eligible"])
        self.assertIn("permission", r["reason"].lower())

    def test_trading_blocked_rejected(self):
        r = se.validate_short_eligibility("AAPL", account=account(trading_blocked=True),
                                          asset=asset(), now=NOW)
        self.assertFalse(r["eligible"])

    def test_symbol_mismatch_rejected(self):
        r = se.validate_short_eligibility("TSLA", account=account(), asset=asset(), now=NOW)
        self.assertFalse(r["eligible"])
        self.assertIn("symbol", r["reason"].lower())

    def test_inactive_asset_rejected(self):
        r = se.validate_short_eligibility("AAPL", account=account(),
                                          asset=asset(status="inactive"), now=NOW)
        self.assertFalse(r["eligible"])

    def test_stale_evidence_rejected(self):
        stale = NOW - timedelta(minutes=10)
        r = se.validate_short_eligibility("AAPL", account=account(), asset=asset(),
                                          now=NOW, retrieved_at=stale,
                                          max_age_seconds=60)
        self.assertFalse(r["eligible"])
        self.assertIn("stale", r["reason"].lower())

    def test_broker_api_failure_fails_closed(self):
        class BrokenTransport:
            def request(self, method, path, payload=None):
                raise RuntimeError("broker down")
        r = se.fetch_short_eligibility("AAPL", BrokenTransport(), now=NOW)
        self.assertFalse(r["eligible"])
        self.assertEqual(r["data_source"], "alpaca")

    def test_fetch_uses_supported_endpoints(self):
        seen = []
        class Transport:
            def request(self, method, path, payload=None):
                seen.append((method, path))
                if path.startswith("/v2/assets"):
                    return asset()
                return account()
        r = se.fetch_short_eligibility("AAPL", Transport(), now=NOW)
        self.assertTrue(r["eligible"], r["reason"])
        self.assertIn(("GET", "/v2/assets/AAPL"), seen)
        self.assertIn(("GET", "/v2/account"), seen)

    def test_hard_to_borrow_ignores_deprecated_flag(self):
        # easy_to_borrow may be true/stale; borrow_status is authoritative.
        data = asset(borrow_status="hard_to_borrow", easy_to_borrow=True)
        r = se.validate_short_eligibility("AAPL", account=account(), asset=data, now=NOW)
        self.assertFalse(r["eligible"])

    def test_no_fabricated_borrow_quantity(self):
        r = se.validate_short_eligibility("AAPL", account=account(), asset=asset(), now=NOW)
        self.assertIsNone(r["available_to_borrow"])


if __name__ == "__main__":
    unittest.main()
