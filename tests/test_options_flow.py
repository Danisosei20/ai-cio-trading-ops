"""Options flow tests (sim-backed service; no network/broker)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.options import OptionContract, OptionQuote
from robinhood_tools.options_flow import pick_long_option, place_long_option
from robinhood_tools.runtime import build_settings
from robinhood_tools.service import RobinhoodTradingService
from robinhood_tools.sim_broker import SimulationBroker
from robinhood_tools.universe import Sp500Snapshot

ET = ZoneInfo("America/New_York")


def quote(symbol="NVDA261120C00240000") -> OptionQuote:
    contract = OptionContract("NVDA", "2026-11-20", Decimal("240"), "call", symbol)
    return OptionQuote(contract, Decimal("3.80"), Decimal("3.95"), Decimal("237.08"),
                       datetime.now(ET).isoformat(), iv=Decimal("0.38"),
                       delta=Decimal("0.46"), open_interest=2000, volume=500)


def snapshot() -> Sp500Snapshot:
    return Sp500Snapshot(frozenset({"NVDA"}), datetime.now(ET).isoformat(),
                         "https://example.com/sp500")


class FakeOptionsData:
    def __init__(self, quotes):
        self.quotes = quotes

    def quote_chain(self, underlying, spot, today, right, expiries=None, steps=3):
        return self.quotes


class OptionsFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = build_settings()
        self.db = CioDatabase(Path(self.temp.name) / "cio.db")
        self.sim = SimulationBroker(
            option_quotes={quote().contract.option_symbol: quote()})
        self.service = RobinhoodTradingService(
            self.sim, approval_store=self.db, sp500_snapshot=snapshot(),
            broker_environment="paper", require_human_confirmation=False)

    def tearDown(self):
        self.temp.cleanup()

    def test_pick_long_option(self):
        best, rejected = pick_long_option(
            data=FakeOptionsData([quote()]), underlying="NVDA",
            spot=Decimal("237.08"), direction="bullish", today=date(2026, 10, 7))
        self.assertEqual(best.contract.option_symbol, "NVDA261120C00240000")
        self.assertEqual(rejected, [])
        with self.assertRaises(PolicyViolation):
            pick_long_option(data=FakeOptionsData([quote()]), underlying="NVDA",
                             spot=Decimal("237.08"), direction="sideways",
                             today=date(2026, 10, 7))

    def test_place_long_option(self):
        result = place_long_option(
            settings=self.settings, snapshot=snapshot(), quote=quote(),
            contracts=1, earnings_date=date(2026, 11, 20),
            premium_cap=Decimal("500"), database=self.db, service=self.service,
            today=date(2026, 10, 7))
        self.assertEqual(result["action"], "buy_placed")
        self.assertEqual(result["status"], "filled")
        self.assertLess(Decimal(result["premium"]), Decimal("500"))

    def test_premium_cap_blocks(self):
        with self.assertRaises(PolicyViolation):
            place_long_option(
                settings=self.settings, snapshot=snapshot(), quote=quote(),
                contracts=10, earnings_date=date(2026, 11, 20),
                premium_cap=Decimal("500"), database=self.db, service=self.service,
                today=date(2026, 10, 7))

    def test_non_member_and_earnings_block(self):
        other = Sp500Snapshot(frozenset({"AAPL"}), datetime.now(ET).isoformat(),
                              "https://example.com/sp500")
        with self.assertRaises(PolicyViolation):
            place_long_option(
                settings=self.settings, snapshot=other, quote=quote(),
                contracts=1, earnings_date=date(2026, 11, 20),
                premium_cap=Decimal("500"), database=self.db, service=self.service,
                today=date(2026, 10, 7))
        with self.assertRaises(PolicyViolation):
            place_long_option(
                settings=self.settings, snapshot=snapshot(), quote=quote(),
                contracts=1, earnings_date=None,
                premium_cap=Decimal("500"), database=self.db, service=self.service,
                today=date(2026, 10, 7))


if __name__ == "__main__":
    unittest.main()
