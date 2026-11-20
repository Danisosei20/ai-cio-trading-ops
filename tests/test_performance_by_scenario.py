"""Real-book performance attribution: does the desk work in ANY scenario?

Pairs the desk's own reconciled fills into round-trips and splits the
result by regime / side / weekday / hold. The pure helpers are pinned
here; the live run writes outputs/simulation/performance_by_scenario.json.
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from scripts.performance_by_scenario import (
    regime_by_day,
    round_trips_with_dates,
    summarise,
)


def fill(symbol, side, qty, price, day):
    return {"symbol": symbol, "side": side, "quantity": str(qty),
            "price": str(price), "fee": "0",
            "filled_at": f"{day}T15:10:00Z"}


class RoundTripPairingTest(unittest.TestCase):
    def test_a_round_trip_keeps_entry_and_exit_dates(self):
        fills = [fill("RDDT270115P00095000", "buy", 2, "11.00", "2026-11-13"),
                 fill("RDDT270115P00095000", "sell", 2, "13.00", "2026-11-14")]
        trips = round_trips_with_dates(fills)
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0]["entry_day"], "2026-11-13")
        self.assertEqual(trips[0]["exit_day"], "2026-11-14")
        self.assertEqual(trips[0]["hold_days"], 1)
        self.assertEqual(trips[0]["pnl"], Decimal("400.00"))
        self.assertEqual(trips[0]["right"], "put")

    def test_a_call_is_labelled_and_an_equity_is_not_a_contract(self):
        fills = [fill("NVDA261218C00240000", "buy", 1, "5.00", "2026-11-13"),
                 fill("NVDA261218C00240000", "sell", 1, "6.00", "2026-11-14"),
                 fill("NVDA", "buy", 10, "100", "2026-11-13"),
                 fill("NVDA", "sell", 10, "110", "2026-11-14")]
        trips = round_trips_with_dates(fills)
        by_sym = {t["symbol"]: t for t in trips}
        self.assertEqual(by_sym["NVDA261218C00240000"]["right"], "call")
        self.assertEqual(by_sym["NVDA"]["right"], "equity")
        # option is 100x, equity is 1x
        self.assertEqual(by_sym["NVDA261218C00240000"]["pnl"], Decimal("100.00"))
        self.assertEqual(by_sym["NVDA"]["pnl"], Decimal("100.00"))

    def test_an_open_buy_is_not_a_round_trip(self):
        fills = [fill("RDDT270115P00095000", "buy", 2, "11.00", "2026-11-13")]
        self.assertEqual(round_trips_with_dates(fills), [])

    def test_two_contracts_on_one_underlier_are_two_round_trips(self):
        fills = [
            fill("RDDT261218P00090000", "buy", 5, "5.70", "2026-11-06"),
            fill("RDDT261218P00090000", "sell", 5, "3.40", "2026-11-10"),
            fill("RDDT270115P00095000", "buy", 2, "10.05", "2026-11-16"),
            fill("RDDT270115P00095000", "sell", 2, "9.10", "2026-11-17"),
        ]
        trips = round_trips_with_dates(fills)
        self.assertEqual(len(trips), 2)
        # (3.40-5.70) x 5 x 100 = -1150 ; (9.10-10.05) x 2 x 100 = -190
        self.assertEqual(sum(t["pnl"] for t in trips), Decimal("-1340.00"))


class RegimeByDayTest(unittest.TestCase):
    def test_labels_follow_the_50_day_mean(self):
        # market_regime needs >=51 closes, so build 60 days of tape.
        rising = [(f"2026-10-{d:02d}", 100.0 + d) for d in range(1, 31)]
        rising += [(f"2026-11-{d:02d}", 130.0 + d) for d in range(1, 31)]
        falling = [(f"2026-12-{d:02d}", 200.0 - d) for d in range(1, 31)]
        spy = rising + falling
        labels = regime_by_day(spy)
        self.assertEqual(labels["2026-11-30"], "RISK_ON")
        self.assertEqual(labels["2026-12-30"], "RISK_OFF")

    def test_short_history_is_unknown_not_on(self):
        labels = regime_by_day([("2026-11-13", 100.0 + i) for i in range(5)])
        self.assertEqual(labels["2026-11-13"], "UNKNOWN")


class SummariseTest(unittest.TestCase):
    def test_counts_and_net(self):
        trips = [{"pnl": Decimal("100")}, {"pnl": Decimal("-50")},
                 {"pnl": Decimal("200")}]
        out = summarise(trips, lambda t: "all")
        self.assertEqual(out["all"]["n"], 3)
        self.assertEqual(out["all"]["net"], 250.0)
        self.assertEqual(out["all"]["wins"], 2)
        self.assertAlmostEqual(out["all"]["win_rate"], 2 / 3, places=2)
        self.assertEqual(out["all"]["best"], 200.0)
        self.assertEqual(out["all"]["worst"], -50.0)


if __name__ == "__main__":
    unittest.main()