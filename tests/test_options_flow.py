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

    def test_all_rejected_selection_exposes_every_rejected_contract(self):
        first = quote("NVDA261120C00240000")
        second = quote("NVDA261120C00250000")
        first = OptionQuote(first.contract, first.bid, first.ask, first.underlying_price,
                            first.quoted_at, iv=first.iv, delta=Decimal("0.10"),
                            open_interest=first.open_interest, volume=first.volume)
        second = OptionQuote(second.contract, second.bid, second.ask, second.underlying_price,
                             second.quoted_at, iv=second.iv, delta=Decimal("0.10"),
                             open_interest=second.open_interest, volume=second.volume)
        with self.assertRaises(PolicyViolation) as raised:
            pick_long_option(
                data=FakeOptionsData([first, second]), underlying="NVDA",
                spot=Decimal("237.08"), direction="bullish", today=date(2026, 10, 7))
        self.assertEqual(
            [item.option_symbol for item in raised.exception.rejected],
            [first.contract.option_symbol, second.contract.option_symbol],
        )

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

    def test_snapshot_none_blocked(self):
        from robinhood_tools.options_flow import place_long_option

        with self.assertRaises(PolicyViolation):
            place_long_option(
                settings=self.settings, snapshot=None, quote=quote(),
                contracts=1, earnings_date=date(2026, 11, 20),
                premium_cap=Decimal("500"), database=self.db, service=self.service,
                today=date(2026, 10, 7))

    def test_empty_allowlist_blocks_etf(self):
        import dataclasses

        from robinhood_tools.paper_flow import SignalOrder, place_signal_order

        bare = dataclasses.replace(self.settings, index_etf_allowlist=())
        with self.assertRaises(PolicyViolation):
            place_signal_order(
                settings=bare, snapshot=snapshot(),
                order=SignalOrder(symbol="SPY", side="buy", quantity=Decimal("1"),
                                  limit_price=Decimal("400.00"),
                                  earnings_date=date(2026, 11, 20)),
                database=self.db, service=self.service, today=date(2026, 10, 6))

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


class CloseOptionTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        from robinhood_tools.database import CioDatabase
        from robinhood_tools.runtime import build_settings
        from robinhood_tools.service import RobinhoodTradingService
        from robinhood_tools.sim_broker import SimulationBroker

        self.temp = tempfile.TemporaryDirectory()
        self.settings = build_settings()
        self.db = CioDatabase(Path(self.temp.name) / "cio.db")
        import datetime as _dt

        from tests.test_options_flow import quote as _q

        self.quote = _q()
        self.sim = SimulationBroker(
            option_quotes={self.quote.contract.option_symbol: self.quote})
        from robinhood_tools.universe import Sp500Snapshot as _Snap

        _snap = _Snap(frozenset({"NVDA"}), _dt.datetime.now(_dt.timezone.utc).isoformat(),
                      "https://example.com/sp500")
        self.service = RobinhoodTradingService(
            self.sim, approval_store=self.db, sp500_snapshot=_snap,
            broker_environment="paper", require_human_confirmation=False)

    def tearDown(self):
        self.temp.cleanup()

    def test_close_round_trip(self):
        from robinhood_tools.options_flow import close_long_option, place_long_option

        import datetime as _dt

        from robinhood_tools.universe import Sp500Snapshot

        snap = Sp500Snapshot(frozenset({"NVDA"}),
                             _dt.datetime.now(_dt.timezone.utc).isoformat(),
                             "https://example.com/sp500")
        buy = place_long_option(
            settings=self.settings, snapshot=snap, quote=self.quote,
            contracts=1, earnings_date=_dt.date(2026, 11, 20),
            premium_cap=Decimal("500"), database=self.db, service=self.service,
            today=_dt.date(2026, 10, 7))
        self.assertEqual(buy["status"], "filled")
        from robinhood_tools.paper_autopilot import build_evidence, claim_candidate
        evidence = build_evidence({
            "candidate_id": "entry-claim", "symbol": "NVDA",
            "occ_symbol": self.quote.contract.option_symbol,
        }, now=_dt.datetime(2026, 10, 7, tzinfo=_dt.timezone.utc))
        self.assertTrue(claim_candidate(self.db.path, evidence))
        out = close_long_option(
            settings=self.settings, quote=self.quote, contracts=1,
            database=self.db, service=self.service, today=_dt.date(2026, 10, 8))
        self.assertEqual(out["action"], "sell_placed")
        self.assertEqual(out["status"], "filled")
        self.assertTrue(out["claim_closed"])
        reopened = dict(evidence, candidate_id="re-entry", premium="1")
        self.assertTrue(claim_candidate(self.db.path, reopened))

    def test_zero_bid_refused(self):
        import copy

        from robinhood_tools.options_flow import close_long_option

        import datetime as _dt

        dead = copy.deepcopy(self.quote)
        object.__setattr__(dead, "bid", Decimal("0"))
        with self.assertRaises(PolicyViolation):
            close_long_option(
                settings=self.settings, quote=dead, contracts=1,
                database=self.db, service=self.service, today=_dt.date(2026, 10, 8))

    def test_negative_contracts_are_rejected(self):
        from robinhood_tools.options_flow import close_long_option

        import datetime as _dt

        with self.assertRaises(PolicyViolation):
            close_long_option(
                settings=self.settings, quote=self.quote, contracts=-1,
                database=self.db, service=self.service, today=_dt.date(2026, 10, 8))

    def test_claim_release_uses_authoritative_order_status(self):
        from unittest.mock import patch

        from robinhood_tools.models import Order
        from robinhood_tools.options_flow import close_long_option, place_long_option
        import datetime as _dt
        from robinhood_tools.paper_autopilot import build_evidence, claim_candidate
        from robinhood_tools.universe import Sp500Snapshot

        snap = Sp500Snapshot(frozenset({"NVDA"}),
                             _dt.datetime.now(_dt.timezone.utc).isoformat(),
                             "https://example.com/sp500")
        place_long_option(settings=self.settings, snapshot=snap, quote=self.quote,
                          contracts=1, earnings_date=_dt.date(2026, 11, 20),
                          premium_cap=Decimal("500"), database=self.db,
                          service=self.service, today=_dt.date(2026, 10, 7))
        evidence = build_evidence({"candidate_id": "authoritative", "symbol": "NVDA",
                                   "occ_symbol": self.quote.contract.option_symbol},
                                  now=_dt.datetime(2026, 10, 7, tzinfo=_dt.timezone.utc))
        self.assertTrue(claim_candidate(self.db.path, evidence))
        authoritative = Order("close-1", "sim-1", self.quote.contract.option_symbol,
                              "sell", "queued")
        with patch.object(self.service.backend, "get_option_order", return_value=authoritative) as get_order:
            out = close_long_option(settings=self.settings, quote=self.quote, contracts=1,
                                    database=self.db, service=self.service,
                                    today=_dt.date(2026, 10, 8))
        get_order.assert_called_once_with(out["order_id"])
        self.assertNotIn("claim_closed", out)
        self.assertTrue(out["reconciliation_required"])


if __name__ == "__main__":
    unittest.main()
