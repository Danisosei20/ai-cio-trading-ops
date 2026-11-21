"""Broker-fill reconciliation: the ledger must prove what filled.

2026-11-16 evidence: 49 approvals, zero order_fills, zero
trade_lifecycles - reconcile_order() existed with tests and no production
caller, so "what did the desk actually do" was answerable only by
querying Alpaca by hand. These tests pin the reconciler against a stubbed
broker (offline, deterministic): fills land, lifecycles drive from
observed fills, orphans/unattributed exits are reported rather than
absorbed, and a second run is a no-op.
"""
from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from robinhood_tools.database import CioDatabase
from robinhood_tools.fill_reconciler import (
    option_multiplier,
    reconcile_desk_fills,
)
from robinhood_tools.reconciliation import Fill


def fill(fill_id: str, qty: str, price: str, at: str = "2026-11-16T15:10:20Z") -> Fill:
    return Fill(fill_id=fill_id, quantity=Decimal(qty), price=Decimal(price),
                fee=Decimal("0"), filled_at=at)


def order(order_id: str, symbol: str, side: str, status: str = "filled") -> dict:
    return {"id": order_id, "symbol": symbol, "side": side, "status": status,
            "qty": "2", "filled_avg_price": "11.00"}


class ReconcilerTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = CioDatabase(Path(self.temp.name) / "cio.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_fills_land_in_the_ledger(self):
        orders = [order("ord-buy-1", "RDDT270115P00095000", "buy")]
        fills = {"ord-buy-1": [fill("f1", "2", "11.00")]}
        # No approval row: the fill still lands, tagged as an orphan order.
        report = reconcile_desk_fills(database=self.db, orders=orders,
                                      fills_by_order=fills,
                                      now_iso="2026-11-16T20:10:20Z")
        self.assertEqual(report.fills_recorded, 1)
        self.assertEqual(report.status, "ok")
        rows = self.db.order_fills("ord-buy-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "RDDT270115P00095000")
        self.assertEqual(rows[0]["quantity"], "2")
        # A fill the ledger never authorized is reported, not absorbed.
        self.assertEqual(report.orphan_orders, ["RDDT270115P00095000:ord-buy-1"])

    def test_pre_tracking_orders_are_not_reported_as_orphans(self):
        # Approval rows only carry order_id from 2026-10-27. Orders from
        # before that date are a bookkeeping gap, not unauthorized trades.
        orders = [order("ord-pre-track", "QQQ261016C00750000", "buy")]
        fills = {"ord-pre-track": [fill("f-pre", "3", "2.10")]}
        report = reconcile_desk_fills(
            database=self.db, orders=orders, fills_by_order=fills,
            historical_order_ids={"ord-pre-track"},
            now_iso="2026-11-21T08:00:00Z")
        self.assertEqual(report.orphan_orders, [])
        self.assertEqual(report.historical_orders, ["QQQ261016C00750000:ord-pre-track"])
        self.assertEqual(report.fills_recorded, 1)

    def test_a_real_orphan_stays_flagged_when_history_is_registered(self):
        orders = [order("ord-unknown", "MRVL261218P00145000", "buy")]
        fills = {"ord-unknown": [fill("f-u", "1", "27.30")]}
        report = reconcile_desk_fills(
            database=self.db, orders=orders, fills_by_order=fills,
            historical_order_ids={"ord-pre-track"},
            now_iso="2026-11-21T08:00:00Z")
        self.assertEqual(report.orphan_orders, ["MRVL261218P00145000:ord-unknown"])
        self.assertEqual(report.historical_orders, [])

    def test_external_bot_order_is_classified_not_a_desk_orphan(self):
        # day-trader-skill/trader-broker places market orders without a
        # client_order_id; Alpaca supplies a plain UUID. Those are legitimate
        # external orders, not desk approval failures — but they must stay
        # visible, never silently absorbed.
        orders = [{"id": "ord-ext", "symbol": "MRVL261218P00145000",
                   "side": "buy", "status": "filled", "qty": "1",
                   "client_order_id": "988e126b-e874-4fb3-a4b6-b0ce0c3bac84"}]
        fills = {"ord-ext": [fill("f-ext", "1", "27.30")]}
        report = reconcile_desk_fills(database=self.db, orders=orders,
                                      fills_by_order=fills)
        self.assertEqual(report.orphan_orders, [])
        self.assertEqual(report.external_orders, ["MRVL261218P00145000:ord-ext"])
        self.assertEqual(report.fills_recorded, 1)

    def test_desk_prefixed_order_without_approval_stays_an_orphan(self):
        orders = [{"id": "ord-desk", "symbol": "NVDA", "side": "buy",
                   "status": "filled", "qty": "2",
                   "client_order_id": "cio-opt-abc123"}]
        fills = {"ord-desk": [fill("f-desk", "2", "100")]}
        report = reconcile_desk_fills(database=self.db, orders=orders,
                                      fills_by_order=fills)
        self.assertEqual(report.orphan_orders, ["NVDA:ord-desk"])
        self.assertEqual(report.external_orders, [])

    def test_buy_fill_drives_a_lifecycle_open(self):
        orders = [order("ord-buy-2", "INTC261120P00055000", "buy")]
        fills = {"ord-buy-2": [fill("f2", "10", "3.10")]}
        reconcile_desk_fills(database=self.db, orders=orders,
                             fills_by_order=fills)
        lifecycle = self.db.get_trade_lifecycle("INTC261120P00055000")
        self.assertEqual(lifecycle["status"], "open")
        self.assertEqual(lifecycle["buy_order_id"], "ord-buy-2")

    def test_sell_fill_closes_with_realized_profit(self):
        # Buy 2 @ 11.00, sell 2 @ 13.00 on a 100x contract = +400.
        orders = [order("ord-buy-3", "RDDT270115P00095000", "buy"),
                  order("ord-sell-3", "RDDT270115P00095000", "sell")]
        fills = {"ord-buy-3": [fill("f3", "2", "11.00")],
                 "ord-sell-3": [fill("f4", "2", "13.00",
                                     "2026-11-17T14:45:00Z")]}
        reconcile_desk_fills(database=self.db, orders=orders,
                             fills_by_order=fills)
        lifecycle = self.db.get_trade_lifecycle("RDDT270115P00095000")
        self.assertEqual(lifecycle["status"], "closed")
        self.assertEqual(Decimal(lifecycle["realized_profit"]), Decimal("400.00"))

    def test_a_losing_close_records_the_loss(self):
        orders = [order("ord-buy-4", "TTWO261218C00240000", "buy"),
                  order("ord-sell-4", "TTWO261218C00240000", "sell")]
        fills = {"ord-buy-4": [fill("f5", "2", "12.00")],
                 "ord-sell-4": [fill("f6", "2", "9.10",
                                     "2026-11-17T14:45:00Z")]}
        reconcile_desk_fills(database=self.db, orders=orders,
                             fills_by_order=fills)
        lifecycle = self.db.get_trade_lifecycle("TTWO261218C00240000")
        # (9.10 - 12.00) x 2 x 100 = -580.
        self.assertEqual(Decimal(lifecycle["realized_profit"]), Decimal("-580.00"))

    def test_unfilled_orders_are_ignored(self):
        orders = [order("ord-open-1", "NVDA", "buy", status="accepted")]
        fills = {"ord-open-1": []}
        report = reconcile_desk_fills(database=self.db, orders=orders,
                                      fills_by_order=fills)
        self.assertEqual(report.filled_orders, 0)
        self.assertEqual(report.fills_recorded, 0)
        self.assertEqual(self.db.order_fills("ord-open-1"), [])

    def test_second_run_records_nothing_new(self):
        orders = [order("ord-buy-5", "MRVL261218P00140000", "buy")]
        fills = {"ord-buy-5": [fill("f7", "1", "16.35")]}
        first = reconcile_desk_fills(database=self.db, orders=orders,
                                     fills_by_order=fills)
        second = reconcile_desk_fills(database=self.db, orders=orders,
                                      fills_by_order=fills)
        self.assertEqual(first.fills_recorded, 1)
        self.assertEqual(second.fills_recorded, 0)
        self.assertEqual(second.fills_already_known, 1)
        self.assertEqual(second.lifecycles_opened, 0)

    def test_sell_with_no_lifecycle_is_reported_not_absorbed(self):
        orders = [order("ord-sell-6", "F261120C00015000", "sell")]
        fills = {"ord-sell-6": [fill("f8", "2", "8.00")]}
        report = reconcile_desk_fills(database=self.db, orders=orders,
                                      fills_by_order=fills)
        self.assertEqual(report.unattributed_exits,
                         ["F261120C00015000:ord-sell-6"])
        with self.assertRaises(Exception):
            self.db.get_trade_lifecycle("F261120C00015000")

    def test_option_multiplier_knows_occ_symbols(self):
        self.assertEqual(option_multiplier("RDDT270115P00095000"), Decimal("100"))
        self.assertEqual(option_multiplier("NVDA"), Decimal("1"))
        self.assertEqual(option_multiplier("brk-b"), Decimal("1"))


if __name__ == "__main__":
    unittest.main()