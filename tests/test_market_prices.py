"""Quote layer tests (fixtures only, no network)."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from robinhood_tools.errors import PolicyViolation
from robinhood_tools.market_prices import from_alpaca_snapshot, get_quote


def snapshot(price: str = "237.10", age_min: int = 1) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=age_min)).isoformat()
    return {"latestTrade": {"p": price, "t": ts},
            "latestQuote": {"bp": "237.05", "ap": "237.15", "t": ts}}


class FakeDataClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def stock_snapshot(self, symbol):
        if self.error:
            raise self.error
        return self.payload


class QuoteTest(unittest.TestCase):
    def test_alpaca_parse(self):
        q = from_alpaca_snapshot("nvda", snapshot())
        self.assertEqual(q.symbol, "NVDA")
        self.assertEqual(q.price, Decimal("237.10"))
        self.assertEqual(q.source, "alpaca-iex")

    def test_incomplete_snapshot_rejected(self):
        with self.assertRaises(PolicyViolation):
            from_alpaca_snapshot("NVDA", {})
        with self.assertRaises(PolicyViolation):
            from_alpaca_snapshot("NVDA", {"latestTrade": {"p": "0", "t": "2026-10-07T00:00:00+00:00"}})

    def test_fresh_alpaca_wins(self):
        q = get_quote("NVDA", data_client=FakeDataClient(snapshot()))
        self.assertEqual(q.source, "alpaca-iex")

    def test_stale_alpaca_fails_closed(self):
        with self.assertRaises(PolicyViolation):
            get_quote("NVDA", data_client=FakeDataClient(snapshot(age_min=30)),
                      max_age_minutes=5)

    def test_broken_alpaca_no_yfinance_fails(self):
        from unittest import mock

        with mock.patch.dict("sys.modules", {"yfinance": None}):
            with self.assertRaises(PolicyViolation):
                get_quote("NVDA", data_client=FakeDataClient(error=RuntimeError("down")))


if __name__ == "__main__":
    unittest.main()
