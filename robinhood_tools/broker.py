"""Broker abstraction (Phase 2 PR 1).

One interface for every venue. Strategy and agent code program to this
Protocol; venue selection is configuration, never a code branch in
strategy code. No strategy module may import a concrete broker.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import Account, CancelResult, Order, OrderReview
from .options import OptionContract, OptionQuote


@runtime_checkable
class Broker(Protocol):
    """Single venue interface: simulation, Alpaca paper, Robinhood."""

    def list_accounts(self) -> list[Account]: ...
    def get_positions(self) -> list[dict]: ...

    def get_option_chain(self, symbol: str) -> list[OptionContract]: ...
    def get_option_quote(self, option_symbol: str) -> OptionQuote: ...

    def review_order(self, request) -> OrderReview:
        """Pre-trade review only. Must never place an order."""
        ...

    def place_order(self, request, review_id: str | None) -> Order:
        """Submit a reviewed order. Resubmission with the same review ID
        must be idempotent (return the original order, no second fill)."""
        ...

    def cancel_order(self, order_id: str) -> CancelResult: ...
    def get_orders(self) -> list[Order]: ...
    def get_fills(self, order_id: str) -> list: ...


def check_conformance(broker: object) -> list[str]:
    """Return missing Broker members (empty = conforms structurally)."""
    required = ("list_accounts", "get_positions", "get_option_chain",
                "get_option_quote", "review_order", "place_order",
                "cancel_order", "get_orders", "get_fills")
    return [name for name in required if not callable(getattr(broker, name, None))]
