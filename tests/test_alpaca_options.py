"""Options read-layer tests (fixtures only, no network)."""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from robinhood_tools.alpaca_options import (monthly_expiries, option_symbol,
                                            snapshot_to_quote, strike_ladder,
                                            third_friday)
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.options import OptionContract, select_long_option


def contract(symbol="NVDA261120C00240000") -> OptionContract:
    return OptionContract("NVDA", "2026-11-20", Decimal("240"), "call", symbol)


def snapshot() -> dict:
    return {"latestQuote": {"bp": "10.43", "ap": "10.71",
                            "t": "2026-10-07T17:15:17.560360006Z"},
            "latestTrade": {"p": "10.55", "t": "2026-10-07T17:15:14.392664077Z"},
            "impliedVolatility": 0.3485,
            "greeks": {"delta": 0.46, "gamma": 0.02, "theta": -0.15, "vega": 0.4},
            "openInterest": 1641, "volume": 500}


class OptionsReadTest(unittest.TestCase):
    def test_third_friday(self):
        self.assertEqual(third_friday(2026, 11), date(2026, 11, 20))
        self.assertEqual(third_friday(2026, 1), date(2026, 1, 16))

    def test_monthly_expiries(self):
        exps = monthly_expiries(date(2026, 10, 7), 3)
        self.assertEqual(exps[0], date(2026, 10, 16))
        self.assertTrue(all(b > a for a, b in zip(exps, exps[1:])))

    def test_occ_symbol(self):
        self.assertEqual(option_symbol("nvda", date(2026, 11, 20), "call", Decimal("240")),
                         "NVDA261120C00240000")
        self.assertEqual(option_symbol("SPY", date(2026, 11, 20), "put", Decimal("685")),
                         "SPY261120P00685000")
        with self.assertRaises(PolicyViolation):
            option_symbol("NVDA", date(2026, 11, 20), "straddle", Decimal("240"))

    def test_strike_ladder(self):
        self.assertEqual(strike_ladder(Decimal("237.13")),
                         [Decimal(v) for v in ("200", "210", "220", "230", "240", "250", "260")])
        self.assertEqual(strike_ladder(Decimal("150"), steps=1),
                         [Decimal("145"), Decimal("150"), Decimal("155")])

    def test_snapshot_mapping(self):
        q = snapshot_to_quote(contract(), snapshot(), Decimal("237.08"))
        self.assertEqual(q.bid, Decimal("10.43"))
        self.assertEqual(q.ask, Decimal("10.71"))
        self.assertEqual(q.delta, Decimal("0.46"))
        self.assertEqual(q.open_interest, 1641)
        self.assertTrue(q.spread_pct() < Decimal("0.10"))

    def test_snapshot_gaps_rejected(self):
        with self.assertRaises(PolicyViolation):
            snapshot_to_quote(contract(), {}, Decimal("237"))
        bad = snapshot()
        del bad["latestQuote"]
        with self.assertRaises(PolicyViolation):
            snapshot_to_quote(contract(), bad, Decimal("237"))

    def test_end_to_end_selection(self):
        quotes = [snapshot_to_quote(
            OptionContract("NVDA", "2026-11-20", Decimal(s), "call",
                           option_symbol("NVDA", date(2026, 11, 20), "call", Decimal(s))),
            {**snapshot(), "latestQuote": {"bp": "9.00", "ap": "9.30",
                                           "t": "2026-10-07T17:15:17Z"},
             "greeks": {"delta": 0.45}}, Decimal("237.08"))
            for s in ("230", "240", "250")]
        best, rejected = select_long_option(quotes, direction="bullish", today="2026-10-07")
        self.assertIsNotNone(best)
        self.assertEqual(rejected, [])


if __name__ == "__main__":
    unittest.main()
