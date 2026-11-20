"""Per-symbol loss memory: a loser gets a rest before it gets re-bought.

Evidence: RDDT lost 40% on 2026-11-10 (two contracts, -825 and -1150) and
the 15:10 watch bought RDDT puts again the same session; TTWO lost 24% on
2026-11-16. The desk's symbol cooldown was time-based only. The fills the
reconciler backfilled (2026-11-16) make round-trip P/L per underlier
computable from the desk's own records, which is what this gates on.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.loss_memory import (
    LossVerdict,
    loss_streak_verdict,
    round_trips,
    underlier,
)


def fill(symbol, side, qty, price, on):
    return {"symbol": symbol, "side": side, "quantity": str(qty),
            "price": str(price), "fee": "0", "filled_at": on}


TODAY = date(2026, 11, 16)


class UnderlierTest(unittest.TestCase):
    def test_occ_symbols_reduce_to_the_underlier(self):
        self.assertEqual(underlier("RDDT270115P00095000"), "RDDT")
        self.assertEqual(underlier("FISV261218C00065000"), "FISV")
        self.assertEqual(underlier("brk-b"), "BRK-B")


class RoundTripTest(unittest.TestCase):
    def test_a_round_trip_computes_realized_pnl(self):
        fills = [fill("RDDT270115P00095000", "buy", 2, "11.00",
                      "2026-11-13T15:10:00Z"),
                 fill("RDDT270115P00095000", "sell", 2, "10.00",
                      "2026-11-14T14:45:00Z")]
        trips = round_trips(fills)
        self.assertEqual(len(trips), 1)
        # (10 - 11) x 2 x 100
        self.assertEqual(trips[0].pnl, Decimal("-200.00"))
        self.assertEqual(trips[0].underlier, "RDDT")

    def test_an_open_buy_is_not_a_round_trip(self):
        fills = [fill("TTWO261218C00240000", "buy", 2, "12.00",
                      "2026-11-12T20:10:00Z")]
        self.assertEqual(round_trips(fills), [])

    def test_two_contracts_of_one_underlier_round_trip_separately(self):
        # The desk's real RDDT week: one contract closed at a loss, the
        # next contract still open (an open buy is not a round-trip).
        fills = [
            fill("RDDT261218P00090000", "buy", 5, "5.70", "2026-11-06T18:25:00Z"),
            fill("RDDT261218P00090000", "sell", 5, "3.40", "2026-11-10T14:45:00Z"),
            fill("RDDT270115P00095000", "buy", 2, "10.05", "2026-11-16T15:10:00Z"),
        ]
        trips = round_trips(fills)
        self.assertEqual([t.underlier for t in trips], ["RDDT"])
        self.assertEqual(trips[0].pnl, Decimal("-1150.00"))

    def test_a_sell_before_any_buy_is_not_a_round_trip(self):
        # FIFO pairing cannot invent an entry: a stray sell is ignored,
        # not paired against nothing.
        fills = [fill("NVDA", "sell", 1, "100", "2026-11-13T14:45:00Z")]
        self.assertEqual(round_trips(fills), [])


class LossStreakVerdictTest(unittest.TestCase):
    def _trips(self, pnls, days_ago=(3, 1)):
        from robinhood_tools.loss_memory import RoundTrip

        out = []
        for pnl, ago in zip(pnls, days_ago):
            day = date(2026, 11, 16 - ago)
            out.append(RoundTrip("RDDT", Decimal("1"), Decimal(str(pnl)), day))
        return out

    def test_two_consecutive_losses_block(self):
        verdict = loss_streak_verdict(self._trips([-100, -50]), symbol="RDDT",
                                      today=TODAY)
        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.streak, 2)
        self.assertIn("2 consecutive losses", verdict.reason)

    def test_a_win_resets_the_streak(self):
        verdict = loss_streak_verdict(self._trips([-100, 200]), symbol="RDDT",
                                      today=TODAY)
        self.assertFalse(verdict.blocked)
        self.assertEqual(verdict.streak, 0)

    def test_one_loss_alone_does_not_block(self):
        verdict = loss_streak_verdict(self._trips([-100]), symbol="RDDT",
                                      today=TODAY)
        self.assertFalse(verdict.blocked)
        self.assertEqual(verdict.streak, 1)

    def test_an_old_streak_is_forgotten(self):
        # Two losses, but the most recent is 40 days ago.
        old = [date(2026, 11, 1), date(2026, 10, 10)]
        from robinhood_tools.loss_memory import RoundTrip

        trips = [RoundTrip("RDDT", Decimal("1"), Decimal("-100"), d) for d in old]
        verdict = loss_streak_verdict(trips, symbol="RDDT", today=TODAY)
        self.assertFalse(verdict.blocked)

    def test_no_history_is_not_a_block(self):
        verdict = loss_streak_verdict([], symbol="NVDA", today=TODAY)
        self.assertFalse(verdict.blocked)
        self.assertIsInstance(verdict, LossVerdict)


class DatabaseGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = CioDatabase(Path(self.temp.name) / "cio.db")

    def tearDown(self):
        self.temp.cleanup()

    def _seed(self, symbol, side, qty, price, on):
        self.db.record_fill(order_id=f"o-{symbol}-{side}-{on}",
                            fill_id=f"f-{symbol}-{side}-{on}", symbol=symbol,
                            side=side, quantity=qty, price=price, fee="0",
                            filled_at=on)

    def test_two_losses_raise_on_the_underlier(self):
        # Two losing round-trips on RDDT inside the window; a NEW contract
        # on the same underlier must be refused.
        self._seed("RDDT261218P00090000", "buy", "5", "5.70", "2026-11-13T15:10:00Z")
        self._seed("RDDT261218P00090000", "sell", "5", "3.40", "2026-11-14T14:45:00Z")
        self._seed("RDDT270115P00095000", "buy", "2", "10.05", "2026-11-15T09:35:00Z")
        self._seed("RDDT270115P00095000", "sell", "2", "8.00", "2026-11-15T14:45:00Z")
        with self.assertRaises(PolicyViolation) as ctx:
            self.db.require_no_symbol_loss_streak("RDDT270115P00100000",
                                                  today="2026-11-16")
        self.assertIn("consecutive losses", str(ctx.exception))

    def test_a_winner_is_unaffected(self):
        self._seed("NVDA", "buy", "10", "100", "2026-11-13T15:10:00Z")
        self._seed("NVDA", "sell", "10", "110", "2026-11-14T14:45:00Z")
        self.db.require_no_symbol_loss_streak("NVDA", today="2026-11-16")

    def test_an_unreadable_date_fails_closed(self):
        with self.assertRaises(PolicyViolation):
            self.db.require_no_symbol_loss_streak("RDDT", today="not-a-date")


if __name__ == "__main__":
    unittest.main()