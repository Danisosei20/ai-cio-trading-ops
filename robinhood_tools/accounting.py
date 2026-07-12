from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .errors import PolicyViolation
from .portfolio import TaxLot, rank_lots_for_tax_aware_sale


@dataclass(frozen=True)
class SaleAccounting:
    proceeds: Decimal
    allocated_cost_basis: Decimal
    fees: Decimal
    realized_profit: Decimal
    quantity: Decimal
    lots: tuple[tuple[date, Decimal], ...]


def account_for_sale(lots: list[TaxLot], *, quantity: Decimal, fill_price: Decimal,
                     fees: Decimal = Decimal("0")) -> SaleAccounting:
    if quantity <= 0 or fill_price <= 0 or fees < 0:
        raise PolicyViolation("Sale quantity/price must be positive and fees cannot be negative.")
    remaining = quantity
    basis = Decimal("0")
    allocations: list[tuple[date, Decimal]] = []
    for lot in rank_lots_for_tax_aware_sale(lots):
        used = min(remaining, lot.quantity)
        if used > 0:
            basis += used * lot.cost_per_share
            allocations.append((lot.acquired, used))
            remaining -= used
        if remaining == 0:
            break
    if remaining > 0:
        raise PolicyViolation("Sale quantity exceeds available tax lots.")
    proceeds = quantity * fill_price
    return SaleAccounting(proceeds, basis, fees, proceeds - basis - fees, quantity, tuple(allocations))
