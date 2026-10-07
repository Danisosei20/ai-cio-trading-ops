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
    bracket = request.take_profit_price is not None or request.bracket_stop_price is not None
    if bracket:
        if request.side != "buy" or request.order_type != "limit":
            raise PolicyViolation("Brackets attach only to limit buy entries.")
        if request.limit_price is None:
            raise PolicyViolation("Bracket entries require a limit price.")
        if request.take_profit_price is None or request.bracket_stop_price is None:
            raise PolicyViolation("Brackets require both take-profit and stop legs.")
        if request.take_profit_price <= request.limit_price:
            raise PolicyViolation("Take-profit must sit above the entry limit.")
        if request.bracket_stop_price >= request.limit_price:
            raise PolicyViolation("Bracket stop must sit below the entry limit.")
        if request.quantity is None or request.quantity != request.quantity.to_integral_value():
            raise PolicyViolation("Brackets require whole-share quantity (no notional/fractional).")


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


def validate_paper_long_option(request: OptionOrderRequest) -> None:
    """Paper-authorized scope: single-leg long openers only.

    No spreads, no naked short, no 0DTE screening here (DTE gates belong to
    the scout + risk layers with real chain data).
    """
    validate_option_order_request(request)
    if len(request.legs) != 1:
        raise PolicyViolation("Paper options are single-leg long only.")
    leg = request.legs[0]
    if leg.side != "buy" or leg.effect != "open":
        raise PolicyViolation("Paper options open long positions only.")


def validate_paper_long_close(request: OptionOrderRequest) -> None:
    """Paper-authorized exits: single-leg closes of long positions."""
    validate_option_order_request(request)
    if len(request.legs) != 1:
        raise PolicyViolation("Paper option exits are single-leg only.")
    leg = request.legs[0]
    if leg.side != "sell" or leg.effect != "close":
        raise PolicyViolation("Paper option exits must sell to close.")


def validate_paper_option_request(request: OptionOrderRequest) -> None:
    """Dispatch openers and closers; reject everything else."""
    validate_option_order_request(request)
    if len(request.legs) != 1:
        raise PolicyViolation("Paper options are single-leg only.")
    leg = request.legs[0]
    if leg.side == "buy" and leg.effect == "open":
        return validate_paper_long_option(request)
    if leg.side == "sell" and leg.effect == "close":
        return validate_paper_long_close(request)
    raise PolicyViolation("Paper options allow buy/open and sell/close only.")


def require_confirmation(confirmation: bool, action: str) -> None:
    if not confirmation:
        raise ConfirmationRequired(f"Explicit confirmation is required before {action}.")


def require_cancel_allowed(account: Account, order: Order) -> None:
    if order.account_id != account.id:
        raise PolicyViolation("The order does not belong to the requested account.")
    if order.status in {"filled", "cancelled", "rejected"}:
        raise PolicyViolation(f"Order {order.id!r} cannot be cancelled because status is {order.status!r}.")
