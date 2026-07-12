from __future__ import annotations

from decimal import Decimal

from .errors import ConfirmationRequired, PolicyViolation
from .models import Account, EquityOrderRequest, OptionOrderRequest, Order


def require_explicit_account(account_id: str | None, accounts: list[Account]) -> Account:
    if not account_id:
        if len(accounts) > 1:
            raise PolicyViolation("account_id is required because multiple Robinhood accounts are available.")
        if not accounts:
            raise PolicyViolation("No Robinhood accounts are available.")
        return accounts[0]

    for account in accounts:
        if account.id == account_id:
            return account
    raise PolicyViolation(f"Account {account_id!r} is not available to this user.")


def require_agentic_account(account: Account) -> None:
    if not account.agentic_allowed:
        raise PolicyViolation(f"Account {account.id!r} is not agentic_allowed=true; trading is blocked.")


def validate_equity_order_request(request: EquityOrderRequest) -> None:
    if request.quantity is None and request.notional is None:
        raise PolicyViolation("Either quantity or notional is required for an equity order.")
    if request.quantity is not None and request.notional is not None:
        raise PolicyViolation("Use quantity or notional, not both, for an equity order.")
    if request.quantity is not None and request.quantity <= Decimal("0"):
        raise PolicyViolation("quantity must be greater than zero.")
    if request.notional is not None and request.notional <= Decimal("0"):
        raise PolicyViolation("notional must be greater than zero.")
    if request.order_type in {"limit", "stop_limit"} and request.limit_price is None:
        raise PolicyViolation("limit_price is required for limit and stop_limit equity orders.")
    if request.order_type in {"stop", "stop_limit"} and request.stop_price is None:
        raise PolicyViolation("stop_price is required for stop and stop_limit equity orders.")


def validate_option_order_request(request: OptionOrderRequest) -> None:
    if not request.legs:
        raise PolicyViolation("At least one option leg is required.")
    if request.order_type != "limit":
        raise PolicyViolation("Option order review requires a limit order to avoid uncontrolled execution risk.")
    if request.limit_price is None:
        raise PolicyViolation("limit_price is required for option order review.")
    for leg in request.legs:
        if leg.quantity <= 0:
            raise PolicyViolation("Each option leg quantity must be greater than zero.")
        if leg.strike_price <= Decimal("0"):
            raise PolicyViolation("Each option leg strike_price must be greater than zero.")


def require_confirmation(confirmation: bool, action: str) -> None:
    if not confirmation:
        raise ConfirmationRequired(f"Explicit confirmation is required before {action}.")


def require_cancel_allowed(account: Account, order: Order) -> None:
    if order.account_id != account.id:
        raise PolicyViolation("The order does not belong to the requested account.")
    if order.status in {"filled", "cancelled", "rejected"}:
        raise PolicyViolation(f"Order {order.id!r} cannot be cancelled because status is {order.status!r}.")
