"""Options order path tests: fingerprint, ledger, guarded placement (sim)."""
from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from robinhood_tools.approvals import option_fingerprint
from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.models import OptionLeg, OptionOrderRequest
from robinhood_tools.service import RobinhoodTradingService
from robinhood_tools.sim_broker import SimulationBroker


def leg(**overrides) -> OptionLeg:
    params = dict(symbol="NVDA261120C00240000", side="buy", effect="open",
                  option_type="call", expiration_date="2026-11-20",
                  strike_price=Decimal("240"), quantity=1)
    params.update(overrides)
    return OptionLeg(**params)


def request(**overrides):
    params = dict(account_id="sim-1", legs=(leg(),), order_type="limit",
                  time_in_force="gfd", limit_price=Decimal("4.10"))
    params.update(overrides)
    return OptionOrderRequest(**params)


class OptionOrderPathTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = CioDatabase(Path(self.temp.name) / "cio.db")
        quote = self._quote()
        self.sim = SimulationBroker(option_quotes={quote.contract.option_symbol: quote})
        self.service = RobinhoodTradingService(
            self.sim, approval_store=self.db, sp500_snapshot=None,
            broker_environment="paper", require_human_confirmation=False)

    def tearDown(self):
        self.temp.cleanup()

    def _quote(self):
        from datetime import datetime, timezone

        from robinhood_tools.options import OptionContract, OptionQuote

        contract = OptionContract("NVDA", "2026-11-20", Decimal("240"), "call",
                                  "NVDA261120C00240000")
        return OptionQuote(contract, Decimal("3.80"), Decimal("3.95"), Decimal("237.08"),
                           datetime.now(timezone.utc).isoformat(), delta=Decimal("0.46"),
                           open_interest=2000, volume=500)

    def test_fingerprint_stable_and_tamper_sensitive(self):
        self.assertEqual(option_fingerprint(request()), option_fingerprint(request()))
        changed = request(limit_price=Decimal("4.20"))
        self.assertNotEqual(option_fingerprint(request()), option_fingerprint(changed))

    def test_full_guarded_path(self):
        req = request()
        review = self.service.review_option_order(req)
        auth = self.db.create_option_approval(req, review, window_minutes=120)
        self.assertEqual(auth.status, "pending")
        self.db.approve(auth.approval_id)
        order = self.service.place_option_order(
            req, review_id=review.review_id, approval_id=auth.approval_id, confirmed=False)
        self.assertEqual(order.status, "filled")
        self.assertEqual(self.db.get(auth.approval_id).status, "executed")

    def test_changed_legs_rejected_at_reserve(self):
        req = request()
        review = self.service.review_option_order(req)
        auth = self.db.create_option_approval(req, review, window_minutes=120)
        self.db.approve(auth.approval_id)
        with self.assertRaises(PolicyViolation):
            self.service.place_option_order(
                request(limit_price=Decimal("9.99")), review_id=review.review_id,
                approval_id=auth.approval_id, confirmed=False)

    def test_short_not_permitted(self):
        bad = OptionOrderRequest(account_id="sim-1",
                                 legs=(leg(side="sell", effect="open"),),
                                 order_type="limit", time_in_force="gfd",
                                 limit_price=Decimal("4.10"))
        with self.assertRaises(PolicyViolation):
            self.service.review_option_order(bad)
        spread = OptionOrderRequest(account_id="sim-1",
                                    legs=(leg(), leg(symbol="NVDA261120P00240000",
                                                     side="buy", effect="open",
                                                     option_type="put")),
                                    order_type="limit", time_in_force="gfd",
                                    limit_price=Decimal("8.00"))
        with self.assertRaises(PolicyViolation):
            self.service.review_option_order(spread)

    def test_live_environment_refuses_options(self):
        live = RobinhoodTradingService(
            self.sim, approval_store=self.db, sp500_snapshot=None,
            broker_environment="live", require_human_confirmation=True)
        with self.assertRaises(PolicyViolation):
            live.review_option_order(request())
        with self.assertRaises(PolicyViolation):
            live.place_option_order(request(), review_id="x", approval_id="y",
                                    confirmed=True)

    def test_sell_to_close_full_path(self):
        buy = request()
        review = self.service.review_option_order(buy)
        auth = self.db.create_option_approval(buy, review, window_minutes=120)
        self.db.approve(auth.approval_id)
        self.service.place_option_order(
            buy, review_id=review.review_id, approval_id=auth.approval_id,
            confirmed=False)
        sell = OptionOrderRequest(account_id="sim-1",
                                  legs=(leg(side="sell", effect="close"),),
                                  order_type="limit", time_in_force="gfd",
                                  limit_price=Decimal("3.00"))
        review2 = self.service.review_option_order(sell)
        auth2 = self.db.create_option_approval(sell, review2, window_minutes=120)
        self.db.approve(auth2.approval_id)
        order = self.service.place_option_order(
            sell, review_id=review2.review_id, approval_id=auth2.approval_id,
            confirmed=False)
        self.assertEqual(order.status, "filled")

    def test_leg_mutations_rejected(self):
        base = request()
        review = self.service.review_option_order(base)
        auth = self.db.create_option_approval(base, review, window_minutes=120)
        self.db.approve(auth.approval_id)
        mutations = [
            {"side": "sell", "effect": "open"},
            {"strike_price": Decimal("250")},
            {"quantity": 2},
            {"expiration_date": "2026-12-18"},
        ]
        import dataclasses

        for i, change in enumerate(mutations):
            new_leg = dataclasses.replace(base.legs[0], **change)
            mutated = OptionOrderRequest(account_id="sim-1", legs=(new_leg,),
                                         order_type="limit", time_in_force="gfd",
                                         limit_price=Decimal("4.10"))
            with self.subTest(mutation=i):
                with self.assertRaises(PolicyViolation):
                    self.service.place_option_order(
                        mutated, review_id=review.review_id,
                        approval_id=auth.approval_id, confirmed=False)

    def test_direction_and_option_id_bound(self):
        import dataclasses

        base = request()
        review = self.service.review_option_order(base)
        auth = self.db.create_option_approval(base, review, window_minutes=120)
        self.db.approve(auth.approval_id)
        for i, change in enumerate(({"direction": "credit"},
                                     {"option_id": "bogus-id-1"})):
            new_leg = dataclasses.replace(base.legs[0], **change)
            mutated = OptionOrderRequest(account_id="sim-1", legs=(new_leg,),
                                         order_type="limit", time_in_force="gfd",
                                         limit_price=Decimal("4.10"))
            with self.subTest(mutation=i):
                with self.assertRaises(PolicyViolation):
                    self.service.place_option_order(
                        mutated, review_id=review.review_id,
                        approval_id=auth.approval_id, confirmed=False)

    def test_legacy_store_fails_closed(self):
        import types

        legacy = types.SimpleNamespace()
        service = RobinhoodTradingService(
            self.sim, approval_store=legacy, sp500_snapshot=None,
            broker_environment="paper", require_human_confirmation=False)
        with self.assertRaises(PolicyViolation):
            service.place_option_order(request(), review_id="r", approval_id="a",
                                       confirmed=False)

    def test_double_reserve_rejected(self):
        req = request()
        review = self.service.review_option_order(req)
        auth = self.db.create_option_approval(req, review, window_minutes=120)
        self.db.approve(auth.approval_id)
        self.service.place_option_order(
            req, review_id=review.review_id, approval_id=auth.approval_id,
            confirmed=False)
        with self.assertRaises(PolicyViolation):
            self.service.place_option_order(
                req, review_id=review.review_id, approval_id=auth.approval_id,
                confirmed=False)


if __name__ == "__main__":
    unittest.main()
