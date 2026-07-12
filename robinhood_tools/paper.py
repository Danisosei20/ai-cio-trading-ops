from __future__ import annotations

import uuid
from decimal import Decimal

from .models import Account, CancelResult, EquityOrderRequest, Order, OrderReview


class PaperTradingBackend:
    """Deterministic simulated broker. It never calls Robinhood."""

    def __init__(self, accounts: list[Account], prices: dict[str, Decimal]):
        self.accounts = accounts
        self.prices = prices
        self.orders: dict[str, Order] = {}

    def list_accounts(self): return self.accounts

    def review_equity_order(self, request: EquityOrderRequest):
        price = self.prices[request.symbol]
        quantity = request.quantity or (request.notional / price if request.notional else None)
        cost = request.notional or (quantity * price if quantity else None)
        return OrderReview(f"paper-review-{uuid.uuid4()}", request.account_id, cost, quantity, raw={"paper": True})

    def place_equity_order(self, request, review_id):
        order = Order(f"paper-order-{uuid.uuid4()}", request.account_id, request.symbol, request.side, "filled", raw={"paper": True, "review_id": review_id})
        self.orders[order.id] = order
        return order

    def get_equity_order(self, order_id): return self.orders[order_id]

    def cancel_equity_order(self, order_id):
        order = self.orders[order_id]
        self.orders[order_id] = Order(order.id, order.account_id, order.symbol, order.side, "cancelled", raw=order.raw)
        return CancelResult(order_id, order.account_id, "cancelled", raw={"paper": True})

    def review_option_order(self, request):
        raise RuntimeError("Options are disabled in paper and live CIO policy.")
