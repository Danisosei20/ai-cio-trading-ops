from __future__ import annotations

from .auth import AuthorizationState, ToolAuthorizer
from .client import RobinhoodBackend
from .errors import PolicyViolation
from .models import CancelResult, EquityOrderRequest, OptionOrderRequest, Order, OrderReview
from .universe import Sp500Snapshot
from .policy import (
    require_agentic_account,
    require_cancel_allowed,
    require_confirmation,
    require_explicit_account,
    validate_equity_order_request,
    validate_option_order_request,
)
from typing import Any, Callable


class RobinhoodTradingService:
    """Safety wrapper around Robinhood trading backend operations."""

    def __init__(
        self,
        backend: RobinhoodBackend,
        authorizer: ToolAuthorizer | None = None,
        approval_store: Any | None = None,
        sp500_snapshot: Sp500Snapshot | None = None,
        execution_guard: Callable[[], None] | None = None,
        broker_environment: str = "live",
        require_human_confirmation: bool = True,
    ):
        self.backend = backend
        self.authorizer = authorizer or ToolAuthorizer(
            AuthorizationState(
                granted_scopes=frozenset(
                    {
                        "robinhood.read",
                        "robinhood.trade_review",
                        "robinhood.trade_write",
                        "robinhood.option_review",
                    }
                )
            )
        )
        self.approval_store = approval_store
        self.sp500_snapshot = sp500_snapshot
        self.execution_guard = execution_guard
        if broker_environment not in {"paper", "live"}:
            raise PolicyViolation("Broker environment must be paper or live.")
        if not require_human_confirmation and broker_environment != "paper":
            raise PolicyViolation("Human confirmation can be disabled only for paper execution.")
        self.broker_environment = broker_environment
        self.require_human_confirmation = require_human_confirmation

    def review_equity_order(self, request: EquityOrderRequest) -> OrderReview:
        self.authorizer.require("robinhood.read", "robinhood.trade_review")
        account = require_explicit_account(request.account_id, self.backend.list_accounts())
        require_agentic_account(account)
        validate_equity_order_request(request)
        if request.side == "buy":
            if not self.sp500_snapshot:
                raise PolicyViolation("Current S&P 500 membership evidence is required before reviewing a purchase.")
            self.sp500_snapshot.require_current_member(request.symbol)
        return self.backend.review_equity_order(request)

    def place_equity_order(
        self,
        request: EquityOrderRequest,
        *,
        review_id: str,
        approval_id: str,
        confirmed: bool,
    ) -> Order:
        self.authorizer.require("robinhood.read", "robinhood.trade_review", "robinhood.trade_write")
        if self.execution_guard:
            self.execution_guard()
        account = require_explicit_account(request.account_id, self.backend.list_accounts())
        require_agentic_account(account)
        validate_equity_order_request(request)
        if not review_id:
            raise PolicyViolation("A review_id is required before placing an equity order.")
        if not approval_id:
            raise PolicyViolation("An approval_id is required before placing an equity order.")
        if not self.approval_store:
            raise PolicyViolation("A durable approval store is required for real order placement.")
        if self.require_human_confirmation:
            require_confirmation(confirmed, "placing a real equity order")
        reserve = getattr(self.approval_store, "reserve_execution", None)
        if reserve:
            reserve(approval_id, request, review_id)
        else:
            self.approval_store.require_for_placement(approval_id, request, review_id)
        try:
            order = self.backend.place_equity_order(request, review_id)
        except Exception as exc:
            reconcile = getattr(self.approval_store, "mark_reconciliation_required", None)
            if reconcile:
                reconcile(approval_id, str(exc))
            raise
        try:
            self.approval_store.mark_executed(approval_id, order.id)
        except TypeError:
            self.approval_store.mark_executed(approval_id)
        return order

    def cancel_equity_order(self, *, account_id: str, order_id: str, confirmed: bool) -> CancelResult:
        self.authorizer.require("robinhood.read", "robinhood.trade_write")
        account = require_explicit_account(account_id, self.backend.list_accounts())
        require_agentic_account(account)
        order = self.backend.get_equity_order(order_id)
        require_cancel_allowed(account, order)
        if self.require_human_confirmation:
            require_confirmation(confirmed, "cancelling a real equity order")
        return self.backend.cancel_equity_order(order_id)

    def review_option_order(self, request: OptionOrderRequest) -> OrderReview:
        self.authorizer.require("robinhood.read", "robinhood.option_review")
        account = require_explicit_account(request.account_id, self.backend.list_accounts())
        require_agentic_account(account)
        validate_option_order_request(request)
        return self.backend.review_option_order(request)
