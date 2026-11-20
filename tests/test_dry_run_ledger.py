"""The risk ledger must be charged by fills, never by attempts.

2026-11-05 dry-run defect: `_call_risk_controller` recorded the order *before*
the `if dry_run:` branch, so every dry-run entry was written to the real
`portfolio_risk_state.json` and nothing ever released it. That was invisible
while a `sell` still popped the long book; once a short lands in
`book["short"]` and that book became the sizing gate, each dry-run short
permanently consumed short budget until every short died at "short notional
budget exhausted" -- the exact failure the branch exists to remove.

The property pinned here: an order is charged when it reaches the broker, and
released when its exit reaches the broker. A dry-run order reaches nothing, so
it is never charged and can never leave the book consumed.

Everything is isolated: the risk ledger, the trade state machine, the engine's
output root and its clock gates all point at a temp dir, and the only "broker"
is a stub that can never send an order.
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import daytrade_engine as de

ET = ZoneInfo("America/New_York")
EQUITY = Decimal("100000")
SHORT_REC = {
    "ticker": "QQQ",
    "setup": "short_breakdown",
    "price": 100.0,
    "or_low": 99.0,
    "or_high": 101.0,
    "vwap": 100.5,
    "zone_verdict": "short_ok",
    "odds": {"available": False, "reason": "time machine long-side only"},
}


@contextmanager
def _isolated(tmp=None):
    """Redirect every write the engine can reach into a temp dir."""
    import robinhood_tools.portfolio_risk as pr
    import robinhood_tools.trade_state as trade_state

    own = tmp is None
    tmp = tmp or Path(tempfile.mkdtemp(prefix="dry-run-ledger-"))
    patchers = [
        patch.object(pr, "STATE_FILE", tmp / "risk_state.json"),
        patch.object(pr, "LOCK_FILE", tmp / "risk.lock"),
        patch.object(pr, "KILL_SWITCH_FILE", tmp / "KILL_SWITCH"),
        patch.object(trade_state, "STATE_FILE", tmp / "trade_state.json"),
        patch.object(de, "ROOT_PATH", tmp),
        patch.object(de, "_load_equity", lambda: EQUITY),
        # the wall clock must not decide what a cycle is allowed to do
        patch.object(de, "_is_no_new_entries_time", lambda now=None: False),
        patch.object(de, "_is_flatten_time", lambda now=None: False),
        patch.object(de, "_log", lambda msg: None),
    ]
    for p in patchers:
        p.start()
    try:
        yield tmp
    finally:
        for p in reversed(patchers):
            p.stop()
        if own:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


@contextmanager
def _stubbed_scout(setups: list[dict]):
    """A scout that reports `setups` without touching the network."""
    import scripts.daytrade_scout as scout

    def _main(argv=None):
        path = de.ROOT_PATH / "outputs" / "daytrade" / \
            f"{datetime.now(ET).date().isoformat()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(__import__("json").dumps({"setups": setups}),
                        encoding="utf-8")
        return 0

    with patch.object(scout, "main", _main):
        yield


class _Frame:
    """The slice of a yfinance frame `manage_exits` reads."""

    def __init__(self, price: float):
        self.empty = False
        self._close = types.SimpleNamespace(iloc=[price])

    def __getitem__(self, key):
        assert key == "Close"
        return self._close


@contextmanager
def _fake_price(price: float):
    """A yfinance whose every ticker prints `price`."""
    mod = types.ModuleType("yfinance")

    class _Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kw):
            return _Frame(price)

    mod.Ticker = _Ticker
    saved = sys.modules.get("yfinance")
    sys.modules["yfinance"] = mod
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = saved


@contextmanager
def _stub_broker(raise_on_place: Exception | None = None):
    """Stub the broker path: placement can never leave this process."""
    saved_modules = {}
    modules = {}

    paper_flow = types.ModuleType("robinhood_tools.paper_flow")

    class SignalOrder:  # test double
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def place_signal_order(**kwargs):
        if raise_on_place is not None:
            raise raise_on_place
        return {"status": "accepted", "order_id": "fake-order-1"}

    paper_flow.SignalOrder = SignalOrder
    paper_flow.place_signal_order = place_signal_order

    runtime = types.ModuleType("robinhood_tools.runtime")
    runtime.build_settings = lambda *a, **k: types.SimpleNamespace(
        index_etf_arbitrage=(), database_path=":memory:")
    runtime.build_paper_service_with_session = lambda **k: object()

    settings_mod = types.ModuleType("robinhood_tools.settings")
    settings_mod.load_env = lambda *a, **k: {}

    database_mod = types.ModuleType("robinhood_tools.database")

    class CioDatabase:  # test double
        def __init__(self, *a, **k):
            pass

        def record_candidate(self, *a, **k):
            pass

        def record_opinion(self, *a, **k):
            pass

        def update_candidate_status(self, *a, **k):
            pass

    database_mod.CioDatabase = CioDatabase

    alpaca = types.ModuleType("robinhood_tools.alpaca_paper")

    class _Transport:
        @classmethod
        def from_values(cls, values):
            return cls()

        def request(self, method, path, payload=None):
            return {"equity": str(EQUITY), "account_number": "PA-FAKE"}

    class _Backend:
        def __init__(self, transport):
            self.transport = transport

    alpaca.AlpacaPaperHttpTransport = _Transport
    alpaca.AlpacaPaperBackend = _Backend
    # `_exit_position` reads these off the engine module, `_place_order`
    # imports them again from the package -- both need the stub.
    modules["robinhood_tools.alpaca_paper"] = alpaca

    modules["robinhood_tools.paper_flow"] = paper_flow
    modules["robinhood_tools.runtime"] = runtime
    modules["robinhood_tools.settings"] = settings_mod
    modules["robinhood_tools.database"] = database_mod

    saved_backend, saved_transport = de.AlpacaPaperBackend, de.AlpacaPaperHttpTransport
    de.AlpacaPaperBackend = _Backend
    de.AlpacaPaperHttpTransport = _Transport
    for name, mod in modules.items():
        saved_modules[name] = sys.modules.get(name)
        sys.modules[name] = mod
    try:
        yield
    finally:
        de.AlpacaPaperBackend, de.AlpacaPaperHttpTransport = saved_backend, saved_transport
        for name, original in saved_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def _exposure():
    import robinhood_tools.portfolio_risk as pr
    return pr.PortfolioRiskController().status()["exposure"]


def _book(name):
    import robinhood_tools.portfolio_risk as pr
    return pr.PortfolioRiskController().state.book[name]


class DryRunNeverConsumesTheLedgerTests(unittest.TestCase):
    def test_dry_run_short_entry_charges_nothing_but_still_cools_down(self):
        import robinhood_tools.portfolio_risk as pr

        with _isolated(), _stubbed_scout([SHORT_REC]):
            result = de.run_cycle(dry_run=True)
            self.assertEqual(len(result["entries"]), 1, result)
            entry = result["entries"][0]
            self.assertEqual(entry["direction"], "short")
            self.assertTrue(entry["dry_run"])
            # never charged: the book is empty and the budget is untouched
            self.assertEqual(_book("short"), {})
            self.assertEqual(_book("long"), {})
            self.assertEqual(_exposure()["short_notional"], 0.0)
            # ... and the attempt *was* noted, so the cooldown still applies
            ctrl = pr.PortfolioRiskController()
            self.assertEqual(
                [(o["symbol"], o["side"]) for o in ctrl.state.recent_orders],
                [("QQQ", "sell")])
            v = ctrl.check("QQQ", "sell", 100, "intraday", short=True)
            self.assertFalse(v.approved)
            self.assertIn("duplicate", v.reason)

    def test_dry_run_exit_does_not_leave_the_book_consumed(self):
        # The property the review names: a session that only ever dry-runs
        # leaves the ledger exactly as it found it, entry and exit.
        with _isolated(), _stubbed_scout([SHORT_REC]), _fake_price(101.5):
            de.run_cycle(dry_run=True)
            self.assertEqual(_exposure()["total_notional"], 0.0)
            exits = de.manage_exits(dry_run=True)
            self.assertEqual(len(exits["exits"]), 1, exits)
            self.assertEqual(exits["exits"][0]["direction"], "short")
            self.assertEqual(exits["exits"][0]["exit_reason"], "stop loss hit")
            # the exit neither charged nor released anything, because the
            # entry charged nothing: the book reads flat, not consumed
            self.assertEqual(_book("short"), {})
            self.assertEqual(_exposure()["short_notional"], 0.0)
            # and the short book is still fully spendable on a real session
            import robinhood_tools.portfolio_risk as pr
            ok, reason = pr.PortfolioRiskController().check_exposure(
                "QQQ", Decimal("5000"), short=True)
            self.assertTrue(ok, reason)


class FillChargesAndReleasesTests(unittest.TestCase):
    def _short_trade(self):
        from robinhood_tools.trade_state import TradeState, TradeStateMachine
        sm = TradeStateMachine()
        trade = sm.create_trade(
            ticker="QQQ", direction="short", setup="short_breakdown",
            strategy="intraday", entry_price=100.0, stop_price=101.0,
            target_price=98.0, qty=50.0,
            client_order_id="test-short-open")
        sm.transition(trade.id, TradeState.SIGNAL_FOUND)
        sm.transition(trade.id, TradeState.RISK_PENDING)
        sm.transition(trade.id, TradeState.ORDER_PENDING)
        sm.transition(trade.id, TradeState.POSITION_OPEN)
        trade.filled_qty = 50.0
        return trade

    def test_live_entry_is_charged_only_once_the_order_reaches_the_broker(self):
        with _isolated(), _stubbed_scout([SHORT_REC]), _stub_broker():
            result = de.run_cycle(dry_run=False)
            self.assertEqual(len(result["entries"]), 1, result)
            self.assertIn("QQQ", _book("short"))
            self.assertEqual(_exposure()["short_notional"], 5000.0)
            # the charge is real, not a label: the next short is capped
            import robinhood_tools.portfolio_risk as pr
            v = pr.PortfolioRiskController().check(
                "TSLA", "sell", 1, "intraday", short=True)
            self.assertFalse(v.approved)
            self.assertIn("short exposure", v.reason)

    def test_a_rejected_order_is_never_charged(self):
        with _isolated(), _stubbed_scout([SHORT_REC]), \
                _stub_broker(raise_on_place=RuntimeError("broker said no")):
            result = de.run_cycle(dry_run=False)
            self.assertEqual(len(result["entries"]), 0, result)
            self.assertTrue(result["blocks"], result)
            self.assertEqual(_book("short"), {})
            self.assertEqual(_exposure()["short_notional"], 0.0)

    def test_a_live_exit_releases_the_short_book(self):
        with _isolated(), _stub_broker():
            self._short_trade()
            de._call_risk_controller(
                {"ticker": "QQQ", "direction": "short", "qty": 50.0,
                 "entry": 100.0}, EQUITY)
            de._record_fill({"ticker": "QQQ", "direction": "short",
                             "qty": 50.0, "entry": 100.0})
            self.assertEqual(_exposure()["short_notional"], 5000.0)
            # price below the target: manage_exits takes the exit and the
            # exit releases the book it was charged to
            with _fake_price(97.0):
                exits = de.manage_exits(dry_run=False)
            self.assertEqual(len(exits["exits"]), 1, exits)
            self.assertEqual(exits["exits"][0]["exit_reason"],
                             "profit target hit")
            self.assertEqual(_book("short"), {})
            self.assertEqual(_exposure()["short_notional"], 0.0)

    def test_sell_to_close_releases_the_long_without_opening_a_short(self):
        from robinhood_tools.trade_state import TradeState, TradeStateMachine
        import robinhood_tools.portfolio_risk as pr

        with _isolated(), _stubbed_scout([SHORT_REC]), _stub_broker():
            sm = TradeStateMachine()
            trade = sm.create_trade(
                ticker="QQQ", direction="long", setup="long_reclaim",
                strategy="intraday", entry_price=100.0, stop_price=99.0,
                target_price=102.0, qty=10.0,
                client_order_id="test-held-long")
            sm.transition(trade.id, TradeState.SIGNAL_FOUND)
            sm.transition(trade.id, TradeState.RISK_PENDING)
            sm.transition(trade.id, TradeState.ORDER_PENDING)
            sm.transition(trade.id, TradeState.POSITION_OPEN)
            trade.filled_qty = 10.0
            pr.PortfolioRiskController().record_order(
                "QQQ", "buy", "intraday", 1000)
            self.assertEqual(_exposure()["long_notional"], 1000.0)

            result = de.run_cycle(dry_run=False)
            entries = result["entries"]
            self.assertEqual(len(entries), 1, result)
            self.assertEqual(entries[0]["action"], "sell_to_close")
            # the long is released and no short was opened for it
            self.assertEqual(_book("long"), {})
            self.assertEqual(_book("short"), {})
            self.assertEqual(_exposure()["total_notional"], 0.0)


if __name__ == "__main__":
    unittest.main()
