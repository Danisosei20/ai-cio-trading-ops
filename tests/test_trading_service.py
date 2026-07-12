from __future__ import annotations

import unittest
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from robinhood_tools.approvals import JsonApprovalStore
from robinhood_tools.auth import AuthorizationState, ToolAuthorizer
from robinhood_tools.errors import (
    ConfirmationRequired,
    ConnectorUnavailable,
    EntitlementMissing,
    PolicyViolation,
)
from robinhood_tools.models import (
    Account,
    CancelResult,
    EquityOrderRequest,
    OptionLeg,
    OptionOrderRequest,
    Order,
    OrderReview,
)
from robinhood_tools.service import RobinhoodTradingService
from robinhood_tools.universe import Sp500Snapshot


class FakeBackend:
    def __init__(self, accounts: list[Account]):
        self.accounts = accounts
        self.placed = []
        self.cancelled = []
        self.orders = {
            "open-1": Order(id="open-1", account_id="agentic", symbol="VOO", side="buy", status="confirmed"),
            "filled-1": Order(id="filled-1", account_id="agentic", symbol="VOO", side="buy", status="filled"),
            "other-1": Order(id="other-1", account_id="non-agentic", symbol="VOO", side="buy", status="confirmed"),
        }

    def list_accounts(self):
        return self.accounts

    def review_equity_order(self, request):
        return OrderReview(
            review_id="review-1",
            account_id=request.account_id,
            estimated_cost=request.notional,
            estimated_quantity=request.quantity,
        )

    def place_equity_order(self, request, review_id):
        self.placed.append((request, review_id))
        return Order(id="order-1", account_id=request.account_id, symbol=request.symbol, side=request.side, status="queued")

    def get_equity_order(self, order_id):
        return self.orders[order_id]

    def cancel_equity_order(self, order_id):
        self.cancelled.append(order_id)
        order = self.orders[order_id]
        return CancelResult(order_id=order_id, account_id=order.account_id, status="cancelled")

    def review_option_order(self, request):
        return OrderReview(review_id="option-review-1", account_id=request.account_id, estimated_cost=request.limit_price)


def equity_request(account_id="agentic"):
    return EquityOrderRequest(
        account_id=account_id,
        symbol="VOO",
        side="buy",
        order_type="market",
        time_in_force="gfd",
        notional=Decimal("100"),
    )


class RobinhoodTradingServiceTests(unittest.TestCase):
    def setUp(self):
        self.accounts = [
            Account(id="agentic", label="Agentic", agentic_allowed=True),
            Account(id="non-agentic", label="Default", agentic_allowed=False),
        ]
        self.backend = FakeBackend(self.accounts)
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.approvals = JsonApprovalStore(Path(self.tempdir.name) / "approvals.json")
        self.sp500 = Sp500Snapshot(
            symbols=frozenset({"VOO", "AAPL"}),
            as_of=datetime.now(timezone.utc).isoformat(),
            source_url="https://www.spglobal.com/spdji/en/indices/equity/sp-500/",
        )
        self.service = RobinhoodTradingService(
            self.backend, approval_store=self.approvals, sp500_snapshot=self.sp500
        )

    def approved(self, request=None, approval_id="approval-1"):
        request = request or equity_request()
        review = self.service.review_equity_order(request)
        self.approvals.create(request, review, window_minutes=120, approval_id=approval_id)
        self.approvals.approve(approval_id)
        return request, review

    def test_review_equity_order_requires_agentic_allowed_account(self):
        with self.assertRaisesRegex(PolicyViolation, "agentic_allowed=true"):
            self.service.review_equity_order(equity_request("non-agentic"))

    def test_purchase_rejects_non_sp500_symbol(self):
        request = EquityOrderRequest(**{**equity_request().__dict__, "symbol": "NOTINDEX"})
        with self.assertRaisesRegex(PolicyViolation, "not verified"):
            self.service.review_equity_order(request)

    def test_purchase_requires_current_membership_evidence(self):
        service = RobinhoodTradingService(self.backend, approval_store=self.approvals)
        with self.assertRaisesRegex(PolicyViolation, "membership evidence"):
            service.review_equity_order(equity_request())

    def test_sale_can_exit_legacy_non_index_holding(self):
        request = EquityOrderRequest(**{**equity_request().__dict__, "symbol": "LEGACY", "side": "sell"})
        review = self.service.review_equity_order(request)
        self.assertEqual(review.review_id, "review-1")

    def test_place_equity_order_requires_review_id_by_default(self):
        with self.assertRaisesRegex(PolicyViolation, "review_id"):
            self.service.place_equity_order(equity_request(), review_id="", approval_id="", confirmed=True)

    def test_place_equity_order_requires_confirmation_even_with_review(self):
        with self.assertRaises(ConfirmationRequired):
            self.service.place_equity_order(
                equity_request(), review_id="review-1", approval_id="approval-1", confirmed=False
            )

    def test_place_equity_order_succeeds_after_review_and_confirmation(self):
        request, review = self.approved()
        order = self.service.place_equity_order(
            request, review_id=review.review_id, approval_id="approval-1", confirmed=True
        )
        self.assertEqual(order.id, "order-1")
        self.assertEqual(len(self.backend.placed), 1)

    def test_place_equity_order_has_no_bypass_review_path(self):
        with self.assertRaises(TypeError):
            self.service.place_equity_order(
                equity_request(), review_id="", approval_id="", confirmed=True, bypass_review=True
            )

    def test_changed_parameters_invalidate_approval(self):
        request, review = self.approved()
        changed = EquityOrderRequest(**{**request.__dict__, "notional": Decimal("101")})
        with self.assertRaisesRegex(PolicyViolation, "parameters changed"):
            self.service.place_equity_order(
                changed, review_id=review.review_id, approval_id="approval-1", confirmed=True
            )

    def test_duplicate_execution_is_blocked(self):
        request, review = self.approved()
        self.service.place_equity_order(
            request, review_id=review.review_id, approval_id="approval-1", confirmed=True
        )
        with self.assertRaisesRegex(PolicyViolation, "executed"):
            self.service.place_equity_order(
                request, review_id=review.review_id, approval_id="approval-1", confirmed=True
            )

    def test_expired_approval_is_blocked(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        store = JsonApprovalStore(Path(self.tempdir.name) / "expired.json", now=lambda: now)
        service = RobinhoodTradingService(self.backend, approval_store=store, sp500_snapshot=self.sp500)
        request = equity_request()
        review = service.review_equity_order(request)
        store.create(request, review, window_minutes=1, approval_id="expired")
        store.now = lambda: now + timedelta(minutes=2)
        with self.assertRaisesRegex(PolicyViolation, "expired"):
            store.approve("expired")

    def test_mismatched_review_id_is_blocked(self):
        request, _ = self.approved()
        with self.assertRaisesRegex(PolicyViolation, "broker review ID"):
            self.service.place_equity_order(
                request, review_id="different", approval_id="approval-1", confirmed=True
            )

    def test_cancel_requires_confirmation(self):
        with self.assertRaises(ConfirmationRequired):
            self.service.cancel_equity_order(account_id="agentic", order_id="open-1", confirmed=False)

    def test_cancel_blocks_filled_orders(self):
        with self.assertRaisesRegex(PolicyViolation, "filled"):
            self.service.cancel_equity_order(account_id="agentic", order_id="filled-1", confirmed=True)

    def test_cancel_blocks_non_agentic_accounts(self):
        with self.assertRaisesRegex(PolicyViolation, "agentic_allowed=true"):
            self.service.cancel_equity_order(account_id="non-agentic", order_id="other-1", confirmed=True)

    def test_cancel_succeeds_for_agentic_open_order_with_confirmation(self):
        result = self.service.cancel_equity_order(account_id="agentic", order_id="open-1", confirmed=True)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.backend.cancelled, ["open-1"])

    def test_multiple_accounts_require_explicit_account_id(self):
        request = equity_request(account_id="")
        with self.assertRaisesRegex(PolicyViolation, "account_id is required"):
            self.service.review_equity_order(request)

    def test_option_review_requires_agentic_account_and_limit_order(self):
        request = OptionOrderRequest(
            account_id="agentic",
            legs=(
                OptionLeg(
                    symbol="VOO",
                    side="buy",
                    effect="open",
                    option_type="call",
                    expiration_date="2026-12-18",
                    strike_price=Decimal("500"),
                    quantity=1,
                ),
            ),
            order_type="limit",
            time_in_force="gfd",
            limit_price=Decimal("2.50"),
            direction="debit",
        )
        review = self.service.review_option_order(request)
        self.assertEqual(review.review_id, "option-review-1")

    def test_connector_unavailable_is_reported_before_backend_call(self):
        service = RobinhoodTradingService(
            self.backend,
            ToolAuthorizer(AuthorizationState(connector_enabled=False, granted_scopes=frozenset())),
            sp500_snapshot=self.sp500,
        )
        with self.assertRaises(ConnectorUnavailable):
            service.review_equity_order(equity_request())

    def test_missing_trade_write_scope_blocks_real_order(self):
        service = RobinhoodTradingService(
            self.backend,
            ToolAuthorizer(
                AuthorizationState(
                    granted_scopes=frozenset({"robinhood.read", "robinhood.trade_review"})
                )
            ),
            sp500_snapshot=self.sp500,
        )
        with self.assertRaises(EntitlementMissing):
            service.place_equity_order(
                equity_request(), review_id="review-1", approval_id="approval-1", confirmed=True
            )


if __name__ == "__main__":
    unittest.main()
