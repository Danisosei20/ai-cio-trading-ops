from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class TaxLot:
    symbol: str
    acquired: date
    quantity: Decimal
    cost_per_share: Decimal
    current_price: Decimal

    @property
    def unrealized_gain(self): return (self.current_price - self.cost_per_share) * self.quantity
    @property
    def long_term(self): return (date.today() - self.acquired).days > 365


def rank_lots_for_tax_aware_sale(lots: list[TaxLot]) -> list[TaxLot]:
    """Estimated tax-aware ordering: losses, then long-term gains, then short-term gains."""
    return sorted(lots, key=lambda lot: (lot.unrealized_gain >= 0, not lot.long_term, lot.unrealized_gain))


def wash_sale_warning(lots: list[TaxLot], recent_purchase_dates: list[date]) -> bool:
    has_loss = any(lot.unrealized_gain < 0 for lot in lots)
    return has_loss and any(abs((date.today() - purchased).days) <= 30 for purchased in recent_purchase_dates)
