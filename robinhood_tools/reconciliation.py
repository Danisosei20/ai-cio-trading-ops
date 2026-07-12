from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .database import CioDatabase
from .models import Order


@dataclass(frozen=True)
class Fill:
    fill_id: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    filled_at: str


@dataclass(frozen=True)
class ReconciliationResult:
    status: str
    new_fills: int
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    fees: Decimal


def reconcile_order(database: CioDatabase, order: Order, fills: list[Fill]) -> ReconciliationResult:
    added = 0
    for fill in fills:
        added += database.record_fill(
            order_id=order.id, fill_id=fill.fill_id, symbol=order.symbol, side=order.side,
            quantity=str(fill.quantity), price=str(fill.price), fee=str(fill.fee), filled_at=fill.filled_at,
        )
    stored = database.order_fills(order.id)
    quantity = sum((Decimal(row["quantity"]) for row in stored), Decimal("0"))
    value = sum((Decimal(row["quantity"]) * Decimal(row["price"]) for row in stored), Decimal("0"))
    fees = sum((Decimal(row["fee"]) for row in stored), Decimal("0"))
    return ReconciliationResult(order.status, added, quantity, value / quantity if quantity else None, fees)
