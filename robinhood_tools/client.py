from __future__ import annotations

from typing import Protocol

from .models import (
    Account,
    CancelResult,
    EquityOrderRequest,
    OptionOrderRequest,
    Order,
    OrderReview,
)


class RobinhoodBackend(Protocol):
    """Adapter boundary for the real Robinhood connector/backend."""

    def list_accounts(self) -> list[Account]:
        """Return all brokerage accounts visible to the authenticated user."""

    def review_equity_order(self, request: EquityOrderRequest) -> OrderReview:
        """Return a pre-trade review for an equity order without placing it."""

    def place_equity_order(self, request: EquityOrderRequest, review_id: str | None) -> Order:
        """Place a real equity order."""

    def get_equity_order(self, order_id: str) -> Order:
        """Return an existing equity order."""

    def cancel_equity_order(self, order_id: str) -> CancelResult:
        """Cancel an existing equity order."""

    def review_option_order(self, request: OptionOrderRequest) -> OrderReview:
        """Return a pre-trade review for an option order without placing it."""
