"""Alpaca options read layer (paper-authorized long-only scope).

No chain-listing endpoint exists on this subscription, so the scout
constructs standard OCC symbols (monthly expirations, strikes around
spot) and quotes them directly via the snapshots endpoint — a pattern
proven live against the paper account. Order placement lives elsewhere;
this module sends no orders and logs no credentials (keys live only in
the transport headers).
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from .errors import PolicyViolation
from .options import OptionContract, OptionQuote

ALPACA_OPTIONS_BASE_URL = "https://data.alpaca.markets"


def third_friday(year: int, month: int) -> date:
    """Monthly equity-option expiration (third Friday)."""
    month_cal = calendar.monthcalendar(year, month)
    fridays = [week[calendar.FRIDAY] for week in month_cal if week[calendar.FRIDAY] != 0]
    return date(year, month, fridays[2])


def monthly_expiries(from_day: date, count: int = 3) -> list[date]:
    """Next `count` monthly expirations on or after `from_day`."""
    out: list[date] = []
    year, month = from_day.year, from_day.month
    while len(out) < count:
        exp = third_friday(year, month)
        if exp >= from_day:
            out.append(exp)
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return out


def option_symbol(underlying: str, expiry: date, right: str, strike: Decimal) -> str:
    """Build an OCC option symbol, e.g. NVDA261120C00240000."""
    if right not in {"call", "put"}:
        raise PolicyViolation("Option right must be call or put.")
    if not underlying.strip():
        raise PolicyViolation("Underlying symbol is required.")
    if strike <= 0:
        raise PolicyViolation("Strike must be positive.")
    cents = int((strike * 1000).to_integral_value())
    return f"{underlying.upper()}{expiry.strftime('%y%m%d')}{'C' if right == 'call' else 'P'}{cents:08d}"


def strike_ladder(spot: Decimal, steps: int = 3) -> list[Decimal]:
    """Strikes around spot ($5 steps under $200, $10 at/above)."""
    if spot <= 0:
        raise PolicyViolation("Spot must be positive.")
    step = Decimal("5") if spot < 200 else Decimal("10")
    base = (spot // step) * step
    return [base + step * i for i in range(-steps, steps + 1)]


def snapshot_to_quote(contract: OptionContract, snapshot: dict,
                      underlying_price: Decimal) -> OptionQuote:
    """Map an Alpaca option snapshot to our OptionQuote. Fail closed."""
    try:
        quote = snapshot["latestQuote"]
        trade = snapshot.get("latestTrade") or {}
        bid = Decimal(str(quote["bp"]))
        ask = Decimal(str(quote["ap"]))
        quoted_at = str(quote.get("t") or trade.get("t") or "")
        iv_raw = snapshot.get("impliedVolatility", snapshot.get("iv"))
        greeks = snapshot.get("greeks") or {}
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise PolicyViolation(
            f"Option snapshot for {contract.option_symbol} is incomplete.") from exc
    if not quoted_at:
        raise PolicyViolation(f"Option snapshot for {contract.option_symbol} lacks a timestamp.")
    daily = snapshot.get("dailyBar") or {}
    try:
        volume = int(daily.get("v", snapshot.get("volume", snapshot.get("dayVolume", 0))) or 0)
    except (TypeError, ValueError):
        volume = 0
    oi_raw = snapshot.get("openInterest", snapshot.get("open_interest"))
    try:
        open_interest = int(oi_raw) if oi_raw is not None else None
        iv = Decimal(str(iv_raw)) if iv_raw is not None else None
        delta = Decimal(str(greeks["delta"])) if greeks.get("delta") is not None else None
        gamma = Decimal(str(greeks["gamma"])) if greeks.get("gamma") is not None else None
        theta = Decimal(str(greeks["theta"])) if greeks.get("theta") is not None else None
        vega = Decimal(str(greeks["vega"])) if greeks.get("vega") is not None else None
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise PolicyViolation(
            f"Option snapshot for {contract.option_symbol} has bad numerics.") from exc
    return OptionQuote(
        contract=contract, bid=bid, ask=ask, underlying_price=underlying_price,
        quoted_at=quoted_at, iv=iv, delta=delta, gamma=gamma, theta=theta,
        vega=vega, open_interest=open_interest, volume=volume)


class AlpacaOptionsData:
    """Read-only option chain + quotes over an authenticated data transport."""

    def __init__(self, key_id: str, secret_key: str):
        if not key_id.strip() or not secret_key.strip():
            raise PolicyViolation("Options data credentials are required.")
        self._key_id = key_id
        self._secret_key = secret_key

    @classmethod
    def from_values(cls, values: dict[str, str]) -> AlpacaOptionsData:
        return cls(values.get("ALPACA_API_KEY", ""), values.get("ALPACA_SECRET_KEY", ""))

    def snapshots(self, symbols: list[str]) -> dict:
        from urllib.request import Request, urlopen
        import json

        from urllib.error import HTTPError, URLError

        from .errors import AuthorizationRequired, ConnectorUnavailable

        if not symbols:
            raise PolicyViolation("At least one option symbol is required.")
        url = (f"{ALPACA_OPTIONS_BASE_URL}/v1beta1/options/snapshots?"
               + urlencode({"symbols": ",".join(symbols)}))
        request = Request(url, method="GET", headers={
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret_key,
            "Accept": "application/json"})
        try:
            with urlopen(request, timeout=15) as response:
                body = response.read()
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise AuthorizationRequired("Alpaca rejected options-data access.") from exc
            raise ConnectorUnavailable(f"Options snapshots returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ConnectorUnavailable("Options data could not be reached.") from exc
        payload = json.loads(body) if body else {}
        if not isinstance(payload, dict) or "snapshots" not in payload:
            raise ConnectorUnavailable("Options snapshot response was invalid.")
        return payload["snapshots"]

    def quote_chain(self, underlying: str, spot: Decimal, today: date,
                    right: str, expiries: list[date] | None = None,
                    steps: int = 3) -> list[OptionQuote]:
        """Build candidate contracts and quote them. Skips unlisted symbols."""
        expiries = expiries or monthly_expiries(today)
        contracts = []
        for exp in expiries:
            for strike in strike_ladder(spot, steps):
                if strike <= 0:
                    continue  # low-priced underlyings: skip, never abort
                contracts.append(OptionContract(
                    underlying.upper(), exp.isoformat(), strike, right,
                    option_symbol(underlying, exp, right, strike)))
        try:
            snaps = self.snapshots([c.option_symbol for c in contracts])
        except Exception as exc:
            from .errors import AuthorizationRequired as _AR
            from .errors import ConnectorUnavailable as _CU

            if isinstance(exc, (_AR, _CU, PolicyViolation)):
                raise
            raise PolicyViolation(f"Chain quote failed: {type(exc).__name__}.") from exc
        quotes = []
        for contract in contracts:
            snap = snaps.get(contract.option_symbol)
            if not snap:
                continue  # unlisted strike/expiry: skip, never invent
            try:
                quotes.append(snapshot_to_quote(contract, snap, spot))
            except PolicyViolation:
                continue
        return quotes


class AlpacaOptionsBackend:
    """Paper-only single-leg long-option orders over the paper transport.

    No live endpoint exists here; the transport rejects every non-paper URL.
    """

    def __init__(self, transport):
        self.transport = transport

    def list_accounts(self) -> list:
        from .models import Account as _Account

        account = self.transport.request("GET", "/v2/account")
        account_id = str(account.get("id") or account.get("account_number") or "")
        if not account_id:
            from .errors import ConnectorUnavailable as _CU

            raise _CU("Alpaca paper account response did not include an account ID.")
        number = str(account.get("account_number") or account_id)
        return [_Account(account_id, "Alpaca Paper", True, account_type="paper",
                         masked_account_number=f"----{number[-4:]}")]

    def review_option_order(self, request) -> "object":
        import uuid as _uuid

        from .approvals import option_fingerprint
        from .models import OrderReview as _Review
        from .policy import validate_paper_option_request

        validate_paper_option_request(request)
        leg = request.legs[0]
        quote = self._quote_for_close(leg.symbol)
        estimated = leg.quantity * (request.limit_price or quote.ask) * 100
        fingerprint = option_fingerprint(request)
        return _Review(
            review_id=f"alpaca-paper-option-review-{fingerprint}-{_uuid.uuid4()}",
            account_id=request.account_id,
            estimated_cost=estimated,
            estimated_quantity=None,
            warnings=("Long only: maximum loss is the premium paid. "
                      "Alpaca paper fills are simulations.",),
            raw={"broker": "alpaca", "environment": "paper",
                 "symbol": leg.symbol.upper(), "order_fingerprint": fingerprint})

    def place_option_order(self, request, review_id: str | None):
        from .approvals import option_fingerprint
        from .errors import ConnectorUnavailable as _CU
        from .errors import PolicyViolation as _PV
        from .models import Order as _Order
        from .policy import validate_paper_option_request

        validate_paper_option_request(request)
        fingerprint = option_fingerprint(request)
        expected_prefix = f"alpaca-paper-option-review-{fingerprint}-"
        if not review_id or not review_id.startswith(expected_prefix):
            raise _PV("A matching fresh Alpaca paper option review is required.")
        leg = request.legs[0]
        payload = {"symbol": leg.symbol.upper(), "qty": str(leg.quantity),
                   "side": leg.side, "type": request.order_type,
                   "time_in_force": {"gfd": "day", "gtc": "gtc"}.get(
                       request.time_in_force, request.time_in_force),
                   "limit_price": str(request.limit_price),
                   "client_order_id": f"cio-opt-{fingerprint[:40]}"}
        result = self.transport.request("POST", "/v2/orders", payload)
        order_id = str(result.get("id") or result.get("order_id") or "")
        if not order_id:
            raise _CU("Alpaca paper option order response lacked an order ID.")
        return _Order(id=order_id, account_id=str(result.get("account_id") or ""),
                      symbol=leg.symbol.upper(), side=leg.side,
                      status="queued", raw=result)

    def get_option_order(self, order_id: str):
        from urllib.parse import quote as _q

        from .models import Order as _Order

        result = self.transport.request("GET", f"/v2/orders/{_q(order_id)}")
        return _Order(id=str(result.get("id") or order_id),
                      account_id=str(result.get("account_id") or ""),
                      symbol=str(result.get("symbol") or ""),
                      side=result.get("side") or "buy",
                      status="queued", raw=result)

    def _quote_for_close(self, option_symbol: str):
        """Latest ask for cost estimates. Fails closed on any gap."""
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        import json

        from urllib.error import HTTPError, URLError

        from .errors import AuthorizationRequired as _AR
        from .errors import ConnectorUnavailable as _CU
        from .errors import PolicyViolation as _PV

        url = (f"{ALPACA_OPTIONS_BASE_URL}/v1beta1/options/snapshots?"
               + urlencode({"symbols": option_symbol}))
        request = Request(url, method="GET", headers={
            "APCA-API-KEY-ID": self.transport.key_id,
            "APCA-API-SECRET-KEY": self.transport.secret_key,
            "Accept": "application/json"})
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read() or b"{}")
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise _AR("Alpaca rejected options-data access.") from exc
            raise _CU(f"Options snapshot returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise _CU("Options data could not be reached.") from exc
        snap = (payload.get("snapshots") or {}).get(option_symbol)
        if not snap:
            raise _PV(f"No option quote for {option_symbol}.")
        try:
            return Decimal(str((snap["latestQuote"])["ap"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise _PV(f"Option quote for {option_symbol} lacks an ask.") from exc
