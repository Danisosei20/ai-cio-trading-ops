from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit"]
TimeInForce = Literal["gfd", "gtc", "ioc", "opg"]
OptionEffect = Literal["open", "close"]
OptionType = Literal["call", "put"]
OrderStatus = Literal["queued", "confirmed", "filled", "partially_filled", "cancelled", "rejected"]


@dataclass(frozen=True)
class Account:
    id: str
    label: str
    agentic_allowed: bool
    account_type: str | None = None
    masked_account_number: str | None = None


@dataclass(frozen=True)
class EquityOrderRequest:
    account_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    quantity: Decimal | None = None
    notional: Decimal | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    extended_hours: bool = False


@dataclass(frozen=True)
class OptionLeg:
    symbol: str
    side: OrderSide
    effect: OptionEffect
    option_type: OptionType
    expiration_date: str
    strike_price: Decimal
    quantity: int
    option_id: str | None = None


@dataclass(frozen=True)
class OptionOrderRequest:
    account_id: str
    legs: tuple[OptionLeg, ...]
    order_type: OrderType
    time_in_force: TimeInForce
    limit_price: Decimal | None = None
    direction: Literal["debit", "credit", "even"] | None = None


@dataclass(frozen=True)
class OrderReview:
    review_id: str
    account_id: str
    estimated_cost: Decimal | None
    estimated_quantity: Decimal | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    expires_at: str | None = None
    raw: dict | None = None


@dataclass(frozen=True)
class Order:
    id: str
    account_id: str
    symbol: str
    side: OrderSide
    status: OrderStatus
    raw: dict | None = None


@dataclass(frozen=True)
class CancelResult:
    order_id: str
    account_id: str
    status: OrderStatus
    raw: dict | None = None
