"""Phase 2 convergence: shared signal-to-paper-order flow tests."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.models import Account
from robinhood_tools.paper import PaperTradingBackend
from robinhood_tools.paper_flow import SignalOrder, place_signal_order
from robinhood_tools.runtime import build_settings
from robinhood_tools.service import RobinhoodTradingService
from robinhood_tools.universe import Sp500Snapshot

ET = ZoneInfo("America/New_York")


def snapshot() -> Sp500Snapshot:
    return Sp500Snapshot(frozenset({"NVDA", "AAPL"}),
                         datetime.now(ET).isoformat(),
                         "https://example.com/sp500")


class PaperFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = build_settings()
        self.db = CioDatabase(Path(self.temp.name) / "cio.db")
        backend = PaperTradingBackend(
            [Account("sim-1", "Sim", True, account_type="paper")],
            {"NVDA": Decimal("230.00"), "SPY": Decimal("600.00"),
             "DIA": Decimal("400.00"), "IWM": Decimal("200.00"),
             "XLK": Decimal("100.00"), "XLF": Decimal("50.00")})
        import dataclasses as _dc

        snap = snapshot()
        if self.settings.index_etf_allowlist:
            snap = _dc.replace(snap, index_etfs=frozenset(self.settings.index_etf_allowlist))
        self.service = RobinhoodTradingService(
            backend, approval_store=self.db, sp500_snapshot=snap,
            broker_environment="paper", require_human_confirmation=False)

    def tearDown(self):
        self.temp.cleanup()

    def place(self, **overrides):
        params = dict(symbol="NVDA", side="buy", quantity=Decimal("2"),
                      limit_price=Decimal("230.00"),
                      earnings_date=date(2026, 11, 20))
        params.update(overrides)
        return place_signal_order(
            settings=self.settings, snapshot=snapshot(),
            order=SignalOrder(**params), database=self.db,
            service=self.service, today=date(2026, 10, 6))

    def test_buy_success(self):
        result = self.place()
        self.assertEqual(result["action"], "buy_placed")
        self.assertEqual(result["status"], "filled")
        self.assertIs(self.service.approval_store, self.db)
        approvals = self.db.list_approvals(10)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["status"], "executed")

    def test_sell_skips_universe_and_earnings(self):
        result = self.place(side="sell")
        self.assertEqual(result["action"], "sell_placed")

    def test_non_member_buy_blocked(self):
        with self.assertRaises(PolicyViolation):
            self.place(symbol="ZZZZ")
        self.assertEqual(self.db.list_approvals(10), [])

    def test_earnings_blocks(self):
        with self.assertRaises(PolicyViolation):
            self.place(earnings_date=date(2026, 10, 8))
        with self.assertRaises(PolicyViolation):
            self.place(earnings_date=None)
        self.assertEqual(self.db.list_approvals(10), [])

    def test_cap_blocks(self):
        with self.assertRaises(PolicyViolation):
            self.place(quantity=Decimal("10"))
        self.assertEqual(self.db.list_approvals(10), [])

    def test_sells_ignore_cap(self):
        result = self.place_with(
            __import__("robinhood_tools.paper_flow", fromlist=["SignalOrder"]).SignalOrder(
                symbol="NVDA", side="sell", quantity=Decimal("10"),
                limit_price=Decimal("230.00")))
        self.assertEqual(result["action"], "sell_placed")

    def test_kill_switch_blocks(self):
        self.db.set_emergency_kill(True)
        with self.assertRaises(PolicyViolation):
            self.place()
        self.assertEqual(self.db.list_approvals(10), [])

    def test_etf_allowlist(self):
        self.assertEqual(tuple(self.settings.index_etf_allowlist),
                         ("SPY", "QQQ", "DIA", "IWM", "XLK", "XLF"))
        spy = self.place(symbol="SPY", side="buy", quantity=Decimal("1"),
                         limit_price=Decimal("400.00"))
        self.assertEqual(spy["action"], "buy_placed")
        for good in ("DIA", "IWM", "XLK", "XLF"):
            allowed = self.place(symbol=good, side="buy", quantity=Decimal("1"),
                                 limit_price=Decimal("400.00"))
            self.assertEqual(allowed["action"], "buy_placed")
        with self.assertRaises(PolicyViolation):
            self.place(symbol="TLT", side="buy", quantity=Decimal("1"),
                       limit_price=Decimal("400.00"))

    def test_bad_shape_blocked(self):
        with self.assertRaises(PolicyViolation):
            self.place(quantity=Decimal("0"))
        with self.assertRaises(PolicyViolation):
            self.place(limit_price=Decimal("-1"))

    def place_with(self, order):
        return place_signal_order(
            settings=self.settings, snapshot=snapshot(),
            order=order, database=self.db,
            service=self.service, today=date(2026, 10, 6))

    def test_bracket_buy_and_stop_triggers(self):
        from robinhood_tools.paper_flow import SignalOrder
        from robinhood_tools.service import RobinhoodTradingService
        from robinhood_tools.sim_broker import SimulationBroker

        sim = SimulationBroker(equity_marks={"NVDA": Decimal("230.00")})
        service = RobinhoodTradingService(
            sim, approval_store=self.db, sp500_snapshot=snapshot(),
            broker_environment="paper", require_human_confirmation=False)
        result = place_signal_order(
            settings=self.settings, snapshot=snapshot(),
            order=SignalOrder(symbol="NVDA", side="buy", quantity=Decimal("2"),
                              limit_price=Decimal("231.00"),
                              earnings_date=date(2026, 11, 20),
                              take_profit_price=Decimal("276.00"),
                              bracket_stop_price=Decimal("211.60")),
            database=self.db, service=service)
        self.assertEqual(result["take_profit"], "276.00")
        self.assertEqual(result["bracket_stop"], "211.60")
        self.assertIn(result["order_id"], sim.brackets)
        # no touch yet
        self.assertEqual(sim.update_marks({"NVDA": Decimal("240.00")}), [])
        # stop hit first
        events = sim.update_marks({"NVDA": Decimal("211.00")})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "stop")
        # target can no longer fire (other leg cancelled by OCO)
        self.assertEqual(sim.update_marks({"NVDA": Decimal("300.00")}), [])

    def test_bracket_target_triggers(self):
        from robinhood_tools.paper_flow import SignalOrder
        from robinhood_tools.service import RobinhoodTradingService
        from robinhood_tools.sim_broker import SimulationBroker

        sim = SimulationBroker(equity_marks={"NVDA": Decimal("230.00")})
        service = RobinhoodTradingService(
            sim, approval_store=self.db, sp500_snapshot=snapshot(),
            broker_environment="paper", require_human_confirmation=False)
        result = place_signal_order(
            settings=self.settings, snapshot=snapshot(),
            order=SignalOrder(symbol="NVDA", side="buy", quantity=Decimal("2"),
                              limit_price=Decimal("231.00"),
                              earnings_date=date(2026, 11, 20),
                              take_profit_price=Decimal("276.00"),
                              bracket_stop_price=Decimal("211.60")),
            database=self.db, service=service)
        events = sim.update_marks({"NVDA": Decimal("280.00")})
        self.assertEqual(events[0]["reason"], "target")
        self.assertEqual(result["status"], "filled")

    def test_bad_bracket_rejected(self):
        from robinhood_tools.paper_flow import SignalOrder

        with self.assertRaises(PolicyViolation):
            self.place_with(SignalOrder(symbol="NVDA", side="buy", quantity=Decimal("2"),
                                        limit_price=Decimal("230.00"),
                                        earnings_date=date(2026, 11, 20),
                                        take_profit_price=Decimal("200.00"),
                                        bracket_stop_price=Decimal("211.60")))

    def test_single_leg_bracket_rejected(self):
        from robinhood_tools.paper_flow import SignalOrder

        with self.assertRaises(PolicyViolation):
            self.place_with(SignalOrder(symbol="NVDA", side="buy", quantity=Decimal("2"),
                                        limit_price=Decimal("231.00"),
                                        earnings_date=date(2026, 11, 20),
                                        take_profit_price=Decimal("276.00")))

    def test_changed_legs_after_review_rejected(self):
        from robinhood_tools.models import EquityOrderRequest
        from robinhood_tools.sim_broker import SimulationBroker

        def eq(limit):
            return EquityOrderRequest(account_id="sim-1", symbol="NVDA", side="buy",
                                      order_type="limit", time_in_force="gfd",
                                      quantity=Decimal("2"),
                                      limit_price=Decimal(limit))

        sim = SimulationBroker(equity_marks={"NVDA": Decimal("230.00")})
        review = sim.review_order(eq("231.00"))
        with self.assertRaises(PolicyViolation):
            sim.place_order(eq("232.00"), review.review_id)


class LookupEarningsTest(unittest.TestCase):
    def setUp(self):
        import sys as _sys
        import types as _types

        self._saved = _sys.modules.get("yfinance")
        self.fake = _types.ModuleType("yfinance")
        _sys.modules["yfinance"] = self.fake

    def tearDown(self):
        import sys as _sys

        if self._saved is None:
            _sys.modules.pop("yfinance", None)
        else:
            _sys.modules["yfinance"] = self._saved

    def test_missing_yfinance_returns_none(self):
        from unittest import mock

        from robinhood_tools.paper_flow import lookup_earnings_date

        with mock.patch.dict("sys.modules", {"yfinance": None}):
            with self.assertRaises(ImportError):
                __import__("yfinance")
            self.assertIsNone(lookup_earnings_date("NVDA"))

    def test_calendar_hit(self):
        from datetime import datetime as _dt

        from robinhood_tools.paper_flow import lookup_earnings_date

        class FakeIloc:
            def __getitem__(self, key):
                return _dt(2026, 11, 20, 8, 0)

        class FakeCal(list):
            @property
            def iloc(self):
                return FakeIloc()

        class FakeTicker:
            def __init__(self, symbol):
                self.symbol = symbol

            @property
            def calendar(self):
                return FakeCal([["Earnings Date"]])

        self.fake.Ticker = FakeTicker
        self.assertEqual(lookup_earnings_date("NVDA"), date(2026, 11, 20))

    def test_earnings_dates_fallback(self):
        from robinhood_tools.paper_flow import lookup_earnings_date

        class FakeIndex:
            def __getitem__(self, i):
                class Day:
                    def date(self):
                        return date(2026, 11, 21)
                return Day()

        class FakeED:
            index = FakeIndex()

            def __len__(self):
                return 1

        class FakeTicker:
            def __init__(self, symbol):
                self.symbol = symbol

            @property
            def calendar(self):
                return None

            @property
            def earnings_dates(self):
                return FakeED()

        self.fake.Ticker = FakeTicker
        self.assertEqual(lookup_earnings_date("NVDA"), date(2026, 11, 21))

    def test_garbage_returns_none(self):
        from robinhood_tools.paper_flow import lookup_earnings_date

        class FakeTicker:
            def __init__(self, symbol):
                self.symbol = symbol

            @property
            def calendar(self):
                raise RuntimeError("boom")

            @property
            def earnings_dates(self):
                raise RuntimeError("boom")

        self.fake.Ticker = FakeTicker
        self.assertIsNone(lookup_earnings_date("NVDA"))


if __name__ == "__main__":
    unittest.main()
