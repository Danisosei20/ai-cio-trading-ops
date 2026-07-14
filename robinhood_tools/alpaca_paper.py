from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .approvals import order_fingerprint
from .errors import AuthorizationRequired, ConnectorUnavailable, PolicyViolation
from .models import Account, CancelResult, EquityOrderRequest, OptionOrderRequest, Order, OrderReview, OrderStatus
from .reconciliation import Fill


ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"


class AlpacaPaperTransport(Protocol):
    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any: ...


class AlpacaPaperHttpTransport:
    """Minimal key-authenticated transport that cannot target Alpaca live trading."""

    def __init__(self, key_id: str, secret_key: str, *, base_url: str = ALPACA_PAPER_BASE_URL):
        if not key_id.strip() or not secret_key.strip():
            raise AuthorizationRequired("Alpaca paper API credentials are required.")
        if base_url.rstrip("/") != ALPACA_PAPER_BASE_URL:
            raise PolicyViolation("Alpaca paper routing must use https://paper-api.alpaca.markets exactly.")
        self.key_id = key_id
        self.secret_key = secret_key
        self.base_url = ALPACA_PAPER_BASE_URL

    @classmethod
    def from_values(cls, values: dict[str, str]) -> AlpacaPaperHttpTransport:
        if values.get("ALPACA_PAPER_TRADE", "true").lower() != "true":
            raise PolicyViolation("ALPACA_PAPER_TRADE must remain true for the paper broker.")
        return cls(
            values.get("ALPACA_API_KEY", ""),
            values.get("ALPACA_SECRET_KEY", ""),
            base_url=values.get("ALPACA_PAPER_BASE_URL", ALPACA_PAPER_BASE_URL),
        )

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/v2/") and path != "/v2/account":
            raise PolicyViolation("Alpaca paper requests must use a supported v2 path.")
        encoded = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}", data=encoded, method=method,
            headers={
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                body = response.read()
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise AuthorizationRequired("Alpaca rejected the paper API credentials or account access.") from exc
            raise ConnectorUnavailable(f"Alpaca paper API returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ConnectorUnavailable("Alpaca paper API could not be reached.") from exc
        return json.loads(body) if body else {}


class AlpacaPaperBackend:
    """Equity-only Alpaca paper broker; it has no Alpaca live endpoint or option path."""

    def __init__(self, transport: AlpacaPaperTransport):
        self.transport = transport

    def list_accounts(self) -> list[Account]:
        account = self._account()
        account_id = str(account.get("id") or account.get("account_number") or "")
        if not account_id:
            raise ConnectorUnavailable("Alpaca paper account response did not include an account ID.")
        number = str(account.get("account_number") or account_id)
        return [Account(
            account_id, "Alpaca Paper", True, account_type="paper",
            masked_account_number=f"----{number[-4:]}",
        )]

    def review_equity_order(self, request: EquityOrderRequest) -> OrderReview:
        self._require_account(request.account_id)
        asset = self.transport.request("GET", f"/v2/assets/{quote(request.symbol.upper())}")
        if asset.get("status") != "active" or not asset.get("tradable", False):
            raise PolicyViolation(f"{request.symbol.upper()} is not active and tradable in Alpaca paper.")
        if asset.get("class") not in {None, "us_equity"}:
            raise PolicyViolation("Alpaca paper routing supports U.S. equities only.")
        fractional = request.notional is not None or (
            request.quantity is not None and request.quantity != request.quantity.to_integral_value()
        )
        if fractional and not asset.get("fractionable", False):
            raise PolicyViolation(f"{request.symbol.upper()} is not fractionable in Alpaca paper.")
        _validate_alpaca_order_shape(request)

        price = request.limit_price or request.stop_price
        estimated_cost = request.notional
        if estimated_cost is None and request.quantity is not None and price is not None:
            estimated_cost = request.quantity * price
        fingerprint = order_fingerprint(request)
        review_id = f"alpaca-paper-review-{fingerprint}-{uuid.uuid4()}"
        return OrderReview(
            review_id=review_id,
            account_id=request.account_id,
            estimated_cost=estimated_cost,
            estimated_quantity=request.quantity,
            warnings=("Alpaca paper fills are simulations and may differ from live execution.",),
            raw={
                "broker": "alpaca", "environment": "paper", "asset_id": asset.get("id"),
                "symbol": request.symbol.upper(), "order_fingerprint": fingerprint,
            },
        )

    def place_equity_order(self, request: EquityOrderRequest, review_id: str | None) -> Order:
        self._require_account(request.account_id)
        _validate_alpaca_order_shape(request)
        fingerprint = order_fingerprint(request)
        expected_prefix = f"alpaca-paper-review-{fingerprint}-"
        if not review_id or not review_id.startswith(expected_prefix):
            raise PolicyViolation("A matching fresh Alpaca paper review is required before placement.")
        payload = _order_payload(request, review_id)
        result = self.transport.request("POST", "/v2/orders", payload)
        return _order_from_result(result, request.account_id, request)

    def get_equity_order(self, order_id: str) -> Order:
        result = self.transport.request("GET", f"/v2/orders/{quote(order_id)}")
        account_id = str(result.get("account_id") or self.list_accounts()[0].id)
        return _order_from_result(result, account_id, None)

    def get_order(self, *, account_id: str, order_id: str) -> Order:
        self._require_account(account_id)
        order = self.get_equity_order(order_id)
        if order.account_id != account_id:
            raise PolicyViolation("The Alpaca paper order does not belong to the selected account.")
        return order

    def get_fills(self, *, account_id: str, order_id: str) -> list[Fill]:
        self._require_account(account_id)
        activities = self.transport.request(
            "GET", "/v2/account/activities?activity_types=FILL&page_size=100&direction=desc",
        )
        if not isinstance(activities, list):
            raise ConnectorUnavailable("Alpaca paper fill activity response was invalid.")
        fills: list[Fill] = []
        for activity in activities:
            if str(activity.get("order_id")) != order_id:
                continue
            required = {"id", "qty", "price", "transaction_time"}
            if not required.issubset(activity):
                raise ConnectorUnavailable("Alpaca paper fill activity is missing required fields.")
            fills.append(Fill(
                fill_id=str(activity["id"]), quantity=Decimal(str(activity["qty"])),
                price=Decimal(str(activity["price"])), fee=Decimal("0"),
                filled_at=str(activity["transaction_time"]),
            ))
        return fills

    def list_positions(self) -> list[dict[str, Any]]:
        positions = self.transport.request("GET", "/v2/positions")
        if not isinstance(positions, list):
            raise ConnectorUnavailable("Alpaca paper positions response was invalid.")
        return positions

    def list_open_orders(self) -> list[dict[str, Any]]:
        orders = self.transport.request("GET", "/v2/orders?status=open&limit=500&direction=asc")
        if not isinstance(orders, list):
            raise ConnectorUnavailable("Alpaca paper orders response was invalid.")
        return orders

    def cancel_equity_order(self, order_id: str) -> CancelResult:
        existing = self.get_equity_order(order_id)
        self.transport.request("DELETE", f"/v2/orders/{quote(order_id)}")
        return CancelResult(order_id, existing.account_id, "cancelled", raw={"broker": "alpaca", "paper": True})

    def review_option_order(self, request: OptionOrderRequest) -> OrderReview:
        raise PolicyViolation("Options are disabled in Alpaca paper and Robinhood live CIO policy.")

    def _account(self) -> dict[str, Any]:
        account = self.transport.request("GET", "/v2/account")
        if not isinstance(account, dict):
            raise ConnectorUnavailable("Alpaca paper account response was invalid.")
        if account.get("account_blocked") or account.get("trading_blocked"):
            raise PolicyViolation("Alpaca paper account is blocked from trading.")
        status = str(account.get("status", "ACTIVE")).upper()
        if status != "ACTIVE":
            raise PolicyViolation(f"Alpaca paper account status is {status}, not ACTIVE.")
        return account

    def _require_account(self, account_id: str) -> None:
        accounts = self.list_accounts()
        if len(accounts) != 1 or accounts[0].id != account_id:
            raise PolicyViolation("The selected account does not match the authenticated Alpaca paper account.")


def _validate_alpaca_order_shape(request: EquityOrderRequest) -> None:
    if request.notional is not None and request.time_in_force != "gfd":
        raise PolicyViolation("Alpaca paper notional orders require gfd/day time in force.")
    if request.extended_hours and (
        request.order_type != "limit" or request.time_in_force not in {"gfd", "gtc"}
    ):
        raise PolicyViolation("Alpaca paper extended-hours orders require a limit order with gfd or gtc.")


def _order_payload(request: EquityOrderRequest, review_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": request.symbol.upper(),
        "side": request.side,
        "type": request.order_type,
        "time_in_force": {"gfd": "day", "gtc": "gtc", "ioc": "ioc", "opg": "opg"}[request.time_in_force],
        "extended_hours": request.extended_hours,
        "client_order_id": f"cio-{hashlib.sha256(review_id.encode()).hexdigest()[:40]}",
    }
    if request.quantity is not None:
        payload["qty"] = str(request.quantity)
    if request.notional is not None:
        payload["notional"] = str(request.notional)
    if request.limit_price is not None:
        payload["limit_price"] = str(request.limit_price)
    if request.stop_price is not None:
        payload["stop_price"] = str(request.stop_price)
    return payload


def _order_from_result(
    result: dict[str, Any], account_id: str, request: EquityOrderRequest | None,
) -> Order:
    order_id = str(result.get("id") or result.get("order_id") or "")
    if not order_id:
        raise ConnectorUnavailable("Alpaca paper order response did not include an order ID.")
    symbol = str(result.get("symbol") or (request.symbol if request else ""))
    side = result.get("side") or (request.side if request else None)
    if side not in {"buy", "sell"}:
        raise ConnectorUnavailable("Alpaca paper order response did not include a valid side.")
    return Order(
        id=order_id,
        account_id=str(result.get("account_id") or account_id),
        symbol=symbol,
        side=side,
        status=_order_status(str(result.get("status", "new"))),
        raw=result,
    )


def _order_status(status: str) -> OrderStatus:
    normalized = status.lower()
    if normalized == "filled":
        return "filled"
    if normalized == "partially_filled":
        return "partially_filled"
    if normalized == "rejected":
        return "rejected"
    if normalized in {"canceled", "cancelled", "expired", "replaced"}:
        return "cancelled"
    return "confirmed" if normalized in {"accepted", "accepted_for_bidding"} else "queued"
