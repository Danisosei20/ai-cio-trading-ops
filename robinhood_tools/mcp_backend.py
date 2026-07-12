from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from .errors import PolicyViolation
from .mcp_config import MCP_TOOLS
from .models import CancelResult, EquityOrderRequest, OptionOrderRequest, Order, OrderReview


class McpToolRunner(Protocol):
    """Small boundary for a host that can invoke Robinhood MCP tools."""

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one MCP tool and return its decoded JSON-like result."""


class McpRobinhoodBackend:
    """RobinhoodBackend implementation that maps safe local models to MCP tools."""

    def __init__(self, runner: McpToolRunner):
        self.runner = runner

    def list_accounts(self):
        raise NotImplementedError(
            "Account listing must be supplied by the host's Robinhood account tool. "
            "Never default an account from the trading endpoint."
        )

    def review_equity_order(self, request: EquityOrderRequest) -> OrderReview:
        result = self.runner.call_tool(MCP_TOOLS["review_equity_order"], _equity_arguments(request))
        return _review_from_result(result, request.account_id)

    def place_equity_order(self, request: EquityOrderRequest, review_id: str | None) -> Order:
        arguments = _equity_arguments(request)
        if review_id:
            arguments["ref_id"] = review_id
        result = self.runner.call_tool(MCP_TOOLS["place_equity_order"], arguments)
        return _order_from_result(result, request)

    def get_equity_order(self, order_id: str) -> Order:
        raise NotImplementedError(
            "Use the host to call _get_equity_orders with both account_number and order_id, "
            "then pass the returned order into the safe cancellation workflow."
        )

    def cancel_equity_order(self, order_id: str) -> CancelResult:
        raise NotImplementedError(
            "Cancellation needs account_number and order_id. Use cancel_equity_order_with_account instead."
        )

    def cancel_equity_order_with_account(self, account_number: str, order_id: str) -> CancelResult:
        result = self.runner.call_tool(
            MCP_TOOLS["cancel_equity_order"],
            {"account_number": account_number, "order_id": order_id},
        )
        return CancelResult(
            order_id=str(result.get("order_id", order_id)),
            account_id=str(result.get("account_number", account_number)),
            status=result.get("state", result.get("status", "cancelled")),
            raw=result,
        )

    def review_option_order(self, request: OptionOrderRequest) -> OrderReview:
        result = self.runner.call_tool(MCP_TOOLS["review_option_order"], _option_arguments(request))
        return _review_from_result(result, request.account_id)


def _equity_arguments(request: EquityOrderRequest) -> dict[str, Any]:
    if request.time_in_force not in {"gfd", "gtc"}:
        raise PolicyViolation("Robinhood MCP equity orders support only gfd or gtc time_in_force.")

    arguments: dict[str, Any] = {
        "account_number": request.account_id,
        "symbol": request.symbol,
        "side": request.side,
        "type": _mcp_equity_order_type(request),
        "time_in_force": request.time_in_force,
        "market_hours": "extended_hours" if request.extended_hours else "regular_hours",
    }
    if request.quantity is not None:
        arguments["quantity"] = str(request.quantity)
    if request.notional is not None:
        arguments["dollar_amount"] = str(request.notional)
    if request.limit_price is not None:
        arguments["limit_price"] = str(request.limit_price)
    if request.stop_price is not None:
        arguments["stop_price"] = str(request.stop_price)
    return arguments


def _option_arguments(request: OptionOrderRequest) -> dict[str, Any]:
    if len(request.legs) != 1:
        raise PolicyViolation("Robinhood MCP option review supports exactly one leg.")
    if request.time_in_force not in {"gfd", "gtc"}:
        raise PolicyViolation("Robinhood MCP option review supports only gfd or gtc time_in_force.")

    leg = request.legs[0]
    if not leg.option_id:
        raise PolicyViolation("option_id from get_option_instruments is required for Robinhood MCP option review.")

    arguments: dict[str, Any] = {
        "account_number": request.account_id,
        "legs": [
            {
                "option_id": leg.option_id,
                "side": leg.side,
                "position_effect": leg.effect,
                "ratio_quantity": leg.quantity,
            }
        ],
        "type": request.order_type,
        "time_in_force": request.time_in_force,
        "quantity": str(leg.quantity),
    }
    if request.limit_price is not None:
        arguments["price"] = str(request.limit_price)
    return arguments


def _mcp_equity_order_type(request: EquityOrderRequest) -> str:
    if request.order_type == "stop":
        return "stop_market"
    return request.order_type


def _review_from_result(result: dict[str, Any], account_id: str) -> OrderReview:
    return OrderReview(
        review_id=str(result.get("ref_id") or result.get("review_id") or result.get("id") or ""),
        account_id=str(result.get("account_number", account_id)),
        estimated_cost=_decimal_or_none(result.get("estimated_cost") or result.get("estimated_total")),
        estimated_quantity=_decimal_or_none(result.get("estimated_quantity") or result.get("quantity")),
        warnings=tuple(result.get("alerts") or result.get("warnings") or ()),
        raw=result,
    )


def _order_from_result(result: dict[str, Any], request: EquityOrderRequest) -> Order:
    return Order(
        id=str(result.get("id") or result.get("order_id") or ""),
        account_id=str(result.get("account_number", request.account_id)),
        symbol=str(result.get("symbol", request.symbol)),
        side=result.get("side", request.side),
        status=result.get("state", result.get("status", "queued")),
        raw=result,
    )


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
