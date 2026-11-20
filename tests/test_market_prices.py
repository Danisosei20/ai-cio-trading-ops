"""Quote layer tests (fixtures only, no network)."""
from __future__ import annotations

import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from robinhood_tools import market_prices
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.market_prices import from_alpaca_snapshot, get_quote, market_regime


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
        # Mock yfinance away: with the market open a live yfinance fallback
        # would return a fresh quote and mask the Alpaca staleness this test
        # exists to check (the suite must pass in any market state).
        with unittest.mock.patch.dict("sys.modules", {"yfinance": None}):
            with self.assertRaises(PolicyViolation):
                get_quote("NVDA", data_client=FakeDataClient(snapshot(age_min=30)),
                          max_age_minutes=5)

    def test_broken_alpaca_no_yfinance_fails(self):
        with unittest.mock.patch.dict("sys.modules", {"yfinance": None}):
            with self.assertRaises(PolicyViolation):
                get_quote("NVDA", data_client=FakeDataClient(error=RuntimeError("down")))


class RegimeTest(unittest.TestCase):
    def setUp(self):
        self.regime = market_regime

    def test_uptrend_risk_on(self):
        self.assertEqual(self.regime([100.0 + i for i in range(60)]), "RISK_ON")

    def test_downtrend_risk_off(self):
        self.assertEqual(self.regime([200.0 - i for i in range(60)]), "RISK_OFF")

    def test_last_close_on_the_50d_mean_is_chop(self):
        self.assertEqual(self.regime([100.0] * 51), "CHOP")

    def test_short_history_unknown(self):
        self.assertEqual(self.regime([100.0] * 10), "UNKNOWN")

    def test_market_regime_risk_off_when_spy_below_50d_sma(self):
        closes = [100.0] * 50 + [50.0]        # last close far below the 50d mean
        self.assertEqual(market_regime(closes), "RISK_OFF")

    def test_market_regime_risk_on_unchanged(self):
        closes = list(range(50, 101))          # rising
        self.assertEqual(market_regime(closes), "RISK_ON")

    def test_spy_closes_returns_empty_list_when_yfinance_missing(self):
        with unittest.mock.patch.dict("sys.modules", {"yfinance": None}):
            self.assertEqual(market_prices._spy_closes(), [])

    def test_spy_closes_for_regime_does_not_fetch_network(self):
        # pinned so a later edit cannot silently add a network call here
        self.assertEqual(market_prices.market_regime([]), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
