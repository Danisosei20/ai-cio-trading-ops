"""Phase 2 PR 1: Broker abstraction + SimulationBroker contract tests."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from robinhood_tools.broker import Broker, check_conformance
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.models import (EquityOrderRequest, OptionLeg,
                                    OptionOrderRequest)
from robinhood_tools.options import (OptionContract, OptionQuote,
                                     check_chain_quality,
                                     select_long_option)
from robinhood_tools.sim_broker import SimulationBroker


def equity(symbol="NVDA", side="buy", qty="2", limit="231.00"):
    return EquityOrderRequest(account_id="sim-1", symbol=symbol, side=side,
                              order_type="limit", time_in_force="gfd",
                              quantity=Decimal(qty), limit_price=Decimal(limit),
                              extended_hours=False)


def call_quote(symbol="NVDA220C00240000", strike="240", bid="3.80", ask="3.95",
               delta="0.46", oi=2000, vol=500, expiry="2026-11-06"):
    contract = OptionContract("NVDA", expiry, Decimal(strike), "call", symbol)
    return OptionQuote(contract, Decimal(bid), Decimal(ask), Decimal("231.45"),
                       datetime.now(timezone.utc).isoformat(),
                       iv=Decimal("0.38"), delta=Decimal(delta),
                       open_interest=oi, volume=vol)


def option_buy(symbol="NVDA220C00240000", qty=1, limit="4.00", side="buy",
               effect=None):
    leg = OptionLeg(symbol=symbol, side=side,
                    effect=effect or ("open" if side == "buy" else "close"),
                    option_type="call",
                    expiration_date="2026-11-06", strike_price=Decimal("240"),
                    quantity=qty)
    return OptionOrderRequest(account_id="sim-1", legs=(leg,), order_type="limit",
                              time_in_force="gfd", limit_price=Decimal(limit))


class BrokerAbstractionTest(unittest.TestCase):
    def test_conformance(self):
        self.assertEqual(check_conformance(SimulationBroker()), [])
        self.assertTrue(isinstance(SimulationBroker(), Broker))

    def test_equity_review_place_filled(self):
        sim = SimulationBroker(equity_marks={"NVDA": Decimal("230.00")})
        req = equity()
        review = sim.review_order(req)
        order = sim.place_order(req, review.review_id)
        self.assertEqual(order.status, "filled")
        self.assertLess(sim.cash, Decimal("100000"))
        self.assertEqual(sim.equity_positions["NVDA"], Decimal("2"))
        self.assertEqual(len(sim.get_fills(order.id)), 1)

    def test_idempotent_resubmit(self):
        sim = SimulationBroker(equity_marks={"NVDA": Decimal("230.00")})
        req = equity()
        review = sim.review_order(req)
        first = sim.place_order(req, review.review_id)
        cash_after = sim.cash
        second = sim.place_order(req, review.review_id)
        self.assertEqual(first.id, second.id)
        self.assertEqual(sim.cash, cash_after)
        self.assertEqual(len(sim.get_fills(first.id)), 1)

    def test_review_required(self):
        sim = SimulationBroker(equity_marks={"NVDA": Decimal("230.00")})
        with self.assertRaises(PolicyViolation):
            sim.place_order(equity(), None)
        with self.assertRaises(PolicyViolation):
            sim.place_order(equity(), "bogus-review-id")

    def test_resting_limit_then_cancel(self):
        sim = SimulationBroker(equity_marks={"NVDA": Decimal("230.00")})
        req = equity(limit="200.00")  # below touch: no fill
        order = sim.place_order(req, sim.review_order(req).review_id)
        self.assertEqual(order.status, "queued")
        result = sim.cancel_order(order.id)
        self.assertEqual(result.status, "cancelled")
        with self.assertRaises(PolicyViolation):
            sim.cancel_order(order.id)  # already terminal

    def test_no_fill_outside_day_range(self):
        sim = SimulationBroker(equity_marks={"NVDA": Decimal("500.00")},
                               day_ranges={"NVDA": (Decimal("228"), Decimal("232"))})
        req = equity(limit="600.00")
        with self.assertRaises(PolicyViolation):
            sim.place_order(req, sim.review_order(req).review_id)

    def test_long_call_max_loss_is_premium(self):
        quote = call_quote()
        sim = SimulationBroker(option_quotes={quote.contract.option_symbol: quote})
        req = option_buy()
        order = sim.place_order(req, sim.review_order(req).review_id)
        self.assertEqual(order.status, "filled")
        spent = Decimal("100000") - sim.cash
        # premium 3.95*1.005*100 + 0.65 fee
        self.assertLess(spent, Decimal("410"))
        # underlying collapses to zero -> worthless expiry
        dead = OptionQuote(quote.contract, Decimal("0"), Decimal("0.01"),
                           Decimal("0.01"), datetime.now(timezone.utc).isoformat())
        sim.update_option_quotes({quote.contract.option_symbol: dead})
        settled = sim.settle_expired("2030-01-01")
        self.assertEqual(len(settled), 1)
        self.assertEqual(Decimal(settled[0]["proceeds"]), Decimal("0"))
        self.assertNotIn(quote.contract.option_symbol, sim.option_positions)

    def test_itm_expiry_settles_intrinsic(self):
        quote = call_quote()
        sim = SimulationBroker(option_quotes={quote.contract.option_symbol: quote})
        req = option_buy()
        sim.place_order(req, sim.review_order(req).review_id)
        itm = OptionQuote(quote.contract, Decimal("9.90"), Decimal("10.10"),
                          Decimal("250.00"), datetime.now(timezone.utc).isoformat())
        sim.update_option_quotes({quote.contract.option_symbol: itm})
        settled = sim.settle_expired("2030-01-01")
        self.assertEqual(Decimal(settled[0]["intrinsic"]), Decimal("10.00"))
        self.assertGreater(Decimal(settled[0]["proceeds"]), Decimal("900"))

    def test_sell_to_close_requires_position(self):
        quote = call_quote()
        sim = SimulationBroker(option_quotes={quote.contract.option_symbol: quote})
        with self.assertRaises(PolicyViolation):
            sim.place_order(option_buy(side="sell"),
                            sim.review_order(option_buy(side="sell")).review_id)

    def test_sell_to_close_works(self):
        quote = call_quote()
        sim = SimulationBroker(option_quotes={quote.contract.option_symbol: quote})
        sim.place_order(option_buy(), sim.review_order(option_buy()).review_id)
        cash_after_buy = sim.cash
        sell = option_buy(side="sell", limit="3.00")
        order = sim.place_order(sell, sim.review_order(sell).review_id)
        self.assertEqual(order.status, "filled")
        self.assertGreater(sim.cash, cash_after_buy)
        self.assertNotIn(quote.contract.option_symbol, sim.option_positions)

    def test_same_review_different_request_rejected(self):
        sim = SimulationBroker(equity_marks={"NVDA": Decimal("230.00")})
        req = equity()
        review = sim.review_order(req)
        sim.place_order(req, review.review_id)
        with self.assertRaises(PolicyViolation):
            sim.place_order(equity(qty="5"), review.review_id)

    def test_settle_without_quote_raises(self):
        quote = call_quote()
        sim = SimulationBroker(option_quotes={quote.contract.option_symbol: quote})
        sim.place_order(option_buy(), sim.review_order(option_buy()).review_id)
        sim.option_quotes.clear()
        with self.assertRaises(PolicyViolation):
            sim.settle_expired("2030-01-01")

    def test_non_limit_equity_rejected(self):
        from robinhood_tools.models import EquityOrderRequest as Req
        sim = SimulationBroker(equity_marks={"NVDA": Decimal("230.00")})
        req = Req(account_id="sim-1", symbol="NVDA", side="buy",
                  order_type="stop", time_in_force="gfd",
                  quantity=Decimal("1"), stop_price=Decimal("229.00"))
        with self.assertRaises(PolicyViolation):
            sim.place_order(req, sim.review_order(req).review_id)

    def test_buy_close_effect_rejected(self):
        quote = call_quote()
        sim = SimulationBroker(option_quotes={quote.contract.option_symbol: quote})
        req = option_buy(side="buy", effect="close")
        with self.assertRaises(PolicyViolation):
            sim.review_order(req)


class ChainQualityTest(unittest.TestCase):
    def test_empty_chain_rejected(self):
        with self.assertRaises(PolicyViolation):
            check_chain_quality([])

    def test_stale_quote_rejected(self):
        quote = call_quote()
        stale = OptionQuote(quote.contract, quote.bid, quote.ask,
                            quote.underlying_price,
                            "2020-01-01T00:00:00+00:00")
        with self.assertRaises(PolicyViolation):
            check_chain_quality([stale])

    def test_wide_spread_rejected_with_reason(self):
        wide = call_quote(bid="1.00", ask="3.00")
        with self.assertRaises(PolicyViolation) as ctx:
            select_long_option([wide], direction="bullish", today="2026-10-06")
        self.assertIn("rejected", str(ctx.exception))

    def test_liquid_contract_selected(self):
        good = call_quote()
        thin = call_quote(symbol="NVDA221C00250000", bid="0.10", ask="0.50",
                          delta="0.20", oi=5, vol=2)
        best, rejected = select_long_option([thin, good], direction="bullish",
                                            today="2026-10-06")
        self.assertEqual(best.contract.option_symbol, good.contract.option_symbol)
        self.assertEqual(len(rejected), 1)


if __name__ == "__main__":
    unittest.main()
