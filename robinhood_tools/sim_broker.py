"""SimulationBroker: project-owned fill simulator behind the Broker interface.

Same interface as real venues; venue choice stays configuration. Models
bid/ask touch fills, spread, slippage, commissions/fees, timestamps,
long-option expiry, and idempotent resubmission. Fills are full quantity
or the order rests (no partial fills yet). Long-only options: maximum
loss is premium paid plus fees.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .broker import Broker  # noqa: F401  (protocol conformance reference)
from .errors import PolicyViolation
from .models import (Account, CancelResult, EquityOrderRequest,
                     OptionOrderRequest, Order, OrderReview)
from .options import OptionQuote, intrinsic_value
from .reconciliation import Fill

CONTRACT_MULTIPLIER = Decimal("100")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _SimOrder:
    order: Order
    review_id: str
    fingerprint: str
    request: object
    filled_qty: Decimal = Decimal("0")
    status: str = "queued"


def _fingerprint(request: object) -> str:
    import hashlib

    return hashlib.sha256(repr(request).encode()).hexdigest()[:16]


@dataclass
class _OptionPosition:
    option_symbol: str
    quantity: int
    avg_premium: Decimal
    right: str
    strike: Decimal
    expiry: str


class SimulationBroker:
    """Deterministic simulator. Never contacts any real broker."""

    def __init__(self, *, cash: Decimal = Decimal("100000"),
                 equity_marks: dict[str, Decimal] | None = None,
                 day_ranges: dict[str, tuple[Decimal, Decimal]] | None = None,
                 option_quotes: dict[str, OptionQuote] | None = None,
                 commission_per_share: Decimal = Decimal("0.005"),
                 fee_per_contract: Decimal = Decimal("0.65"),
                 slippage_bps: Decimal = Decimal("5")):
        self.cash = cash
        self.equity_marks = {k.upper(): v for k, v in (equity_marks or {}).items()}
        self.day_ranges = {k.upper(): v for k, v in (day_ranges or {}).items()}
        self.option_quotes = dict(option_quotes or {})
        self.commission_per_share = commission_per_share
        self.fee_per_contract = fee_per_contract
        self.slip = slippage_bps / Decimal("10000")
        self._account = Account("sim-1", "Simulation", True, account_type="simulated",
                                masked_account_number="----SIM1")
        self._orders: dict[str, _SimOrder] = {}  # by review_id (idempotency)
        self._by_id: dict[str, _SimOrder] = {}
        self._fills: dict[str, list[Fill]] = {}
        self.equity_positions: dict[str, Decimal] = {}
        self.option_positions: dict[str, _OptionPosition] = {}
        self.brackets: dict[str, dict] = {}  # order_id -> OCO children state
        self._review_prints: dict[str, str] = {}  # review_id -> request fingerprint

    # -- venue info ------------------------------------------------------
    def list_accounts(self) -> list[Account]:
        return [self._account]

    def get_positions(self) -> list[dict]:
        out = [{"symbol": s, "qty": str(q), "kind": "equity"}
               for s, q in self.equity_positions.items() if q != 0]
        for p in self.option_positions.values():
            quote = self.option_quotes.get(p.option_symbol)
            mark = quote.mid() if quote else p.avg_premium
            out.append({"symbol": p.option_symbol, "qty": str(p.quantity),
                        "kind": "option", "avg_premium": str(p.avg_premium),
                        "mark": str(mark)})
        return out

    def get_option_chain(self, symbol: str) -> list:
        return [q.contract for q in self.option_quotes.values()
                if q.contract.underlying == symbol.upper()]

    def get_option_quote(self, option_symbol: str) -> OptionQuote:
        try:
            return self.option_quotes[option_symbol]
        except KeyError as exc:
            raise PolicyViolation(f"Unknown option {option_symbol}.") from exc

    def update_option_quotes(self, quotes: dict[str, OptionQuote]) -> None:
        self.option_quotes.update(quotes)

    # -- orders ----------------------------------------------------------
    def review_order(self, request) -> OrderReview:
        if isinstance(request, EquityOrderRequest):
            return self._review_equity(request)
        if isinstance(request, OptionOrderRequest):
            return self._review_option(request)
        raise PolicyViolation("SimulationBroker supports equity and single-leg long option orders.")

    def _review_equity(self, request: EquityOrderRequest) -> OrderReview:
        mark = self._mark(request.symbol)
        qty = request.quantity
        if qty is None and request.notional is not None:
            qty = request.notional / mark
        if not qty or qty <= 0:
            raise PolicyViolation("Quantity or notional is required.")
        cost = request.notional or qty * (request.limit_price or mark)
        review_id = f"sim-review-{uuid.uuid4()}"
        self._review_prints[review_id] = _fingerprint(request)
        return OrderReview(review_id, request.account_id,
                           cost, qty, raw={"sim": True})

    def _review_option(self, request: OptionOrderRequest) -> OrderReview:
        leg = self._single_long_leg(request)
        quote = self.get_option_quote(leg.symbol)
        cost = Decimal(leg.quantity) * (request.limit_price or quote.ask) * CONTRACT_MULTIPLIER
        review_id = f"sim-review-{uuid.uuid4()}"
        self._review_prints[review_id] = _fingerprint(request)
        return OrderReview(review_id, request.account_id,
                           cost, Decimal(leg.quantity), raw={"sim": True})

    def place_order(self, request, review_id: str | None) -> Order:
        if not review_id or not review_id.startswith("sim-review-"):
            raise PolicyViolation("A matching simulation review is required before placement.")
        expected = self._review_prints.get(review_id)
        if expected is not None and expected != _fingerprint(request):
            raise PolicyViolation("Review does not match this order; changed legs rejected.")
        if review_id in self._orders:
            existing = self._orders[review_id]
            if existing.fingerprint != _fingerprint(request):
                raise PolicyViolation(
                    "Review ID already used for a different order; refusing (not idempotent).")
            return existing.order  # idempotent resubmit
        if isinstance(request, EquityOrderRequest):
            order = self._place_equity(request, review_id)
        elif isinstance(request, OptionOrderRequest):
            order = self._place_option(request, review_id)
        else:
            raise PolicyViolation("Unsupported order request type.")
        return order

    def _place_equity(self, request: EquityOrderRequest, review_id: str) -> Order:
        if request.order_type not in {"market", "limit"}:
            raise PolicyViolation("Simulation supports market and limit equity orders.")
        mark = self._mark(request.symbol)
        qty = request.quantity or Decimal("0")
        if request.order_type == "limit" and request.limit_price is not None:
            touch = mark * (1 + self.slip) if request.side == "buy" else mark * (1 - self.slip)
            if request.side == "buy" and request.limit_price < touch:
                return self._resting(request, review_id, qty)
            if request.side == "sell" and request.limit_price > touch:
                return self._resting(request, review_id, qty)
            fill_px = min(request.limit_price, touch) if request.side == "buy" else max(request.limit_price, touch)
        else:
            fill_px = mark * (1 + self.slip) if request.side == "buy" else mark * (1 - self.slip)
        self._check_range(request.symbol, fill_px)
        notional = qty * fill_px
        fee = qty * self.commission_per_share
        if request.side == "buy":
            if self.cash < notional + fee:
                raise PolicyViolation("Insufficient simulation cash.")
            self.cash -= notional + fee
            self.equity_positions[request.symbol.upper()] = self.equity_positions.get(request.symbol.upper(), Decimal("0")) + qty
        else:
            held = self.equity_positions.get(request.symbol.upper(), Decimal("0"))
            if held < qty:
                raise PolicyViolation("Cannot sell more than held in simulation.")
            self.equity_positions[request.symbol.upper()] = held - qty
            self.cash += notional - fee
        order = self._filled(request, review_id, qty, fill_px, fee)
        bracket_tp = getattr(request, "take_profit_price", None)
        bracket_stop = getattr(request, "bracket_stop_price", None)
        if request.side == "buy" and (bracket_tp is not None or bracket_stop is not None):
            self.brackets[order.id] = {"symbol": request.symbol.upper(), "qty": qty,
                                       "take_profit": bracket_tp, "stop": bracket_stop,
                                       "status": "open"}
        return order

    def _place_option(self, request: OptionOrderRequest, review_id: str) -> Order:
        leg = self._single_long_leg(request)
        quote = self.get_option_quote(leg.symbol)
        if leg.side == "sell":
            held = self.option_positions.get(leg.symbol)
            if held is None or held.quantity < leg.quantity:
                raise PolicyViolation("Options are long-only in simulation; sell only to close.")
            fill_px = quote.bid * (1 - self.slip)
            if request.limit_price is not None and request.limit_price > fill_px:
                return self._resting_option(request, review_id, leg)
            self._check_option_fill(quote, fill_px)
            proceeds = Decimal(leg.quantity) * fill_px * CONTRACT_MULTIPLIER
            fee = Decimal(leg.quantity) * self.fee_per_contract
            self.cash += proceeds - fee
            held.quantity -= leg.quantity
            if held.quantity <= 0:
                del self.option_positions[leg.symbol]
            return self._filled_option(request, review_id, leg, fill_px, fee)
        ask_touch = quote.ask * (1 + self.slip)
        if request.limit_price is not None and request.limit_price < ask_touch:
            return self._resting_option(request, review_id, leg)
        fill_px = min(request.limit_price, ask_touch) if request.limit_price else ask_touch
        self._check_option_fill(quote, fill_px)
        cost = Decimal(leg.quantity) * fill_px * CONTRACT_MULTIPLIER
        fee = Decimal(leg.quantity) * self.fee_per_contract
        if self.cash < cost + fee:
            raise PolicyViolation("Insufficient simulation cash for premium + fees.")
        self.cash -= cost + fee
        held = self.option_positions.get(leg.symbol)
        if held:
            total = held.quantity + leg.quantity
            held.avg_premium = (held.avg_premium * held.quantity + fill_px * leg.quantity) / total
            held.quantity = total
        else:
            self.option_positions[leg.symbol] = _OptionPosition(
                leg.symbol, leg.quantity, fill_px, quote.contract.right,
                quote.contract.strike, quote.contract.expiry)
        return self._filled_option(request, review_id, leg, fill_px, fee)

    def cancel_order(self, order_id: str) -> CancelResult:
        sim = self._by_id.get(order_id)
        if sim is None:
            raise PolicyViolation("Unknown simulation order.")
        if sim.status != "queued":
            raise PolicyViolation("Only resting simulation orders can be cancelled.")
        sim.status = "cancelled"
        order = Order(sim.order.id, sim.order.account_id, sim.order.symbol,
                      sim.order.side, "cancelled", raw={**(sim.order.raw or {}), "sim": True})
        sim.order = order
        return CancelResult(order_id, order.account_id, "cancelled", raw={"sim": True})

    def get_orders(self) -> list[Order]:
        return [s.order for s in self._by_id.values()]

    def get_fills(self, order_id: str) -> list[Fill]:
        return list(self._fills.get(order_id, []))

    def _check_option_fill(self, quote: OptionQuote, price: Decimal) -> None:
        lo = quote.bid * (1 - self.slip)
        hi = quote.ask * (1 + self.slip)
        if not lo <= price <= hi:
            raise PolicyViolation(f"Option fill {price} outside quoted range; refusing.")

    def update_marks(self, marks: dict[str, Decimal]) -> list[dict]:
        """Evaluate resting bracket children; first touch wins, other cancels."""
        for symbol, mark in marks.items():
            self.equity_marks[symbol.upper()] = mark
        events = []
        for order_id, bracket in list(self.brackets.items()):
            if bracket["status"] != "open":
                continue
            current = self.equity_marks.get(bracket["symbol"])
            if current is None:
                continue
            exit_px: Decimal | None = None
            reason = ""
            if bracket["stop"] is not None and current <= bracket["stop"]:
                exit_px, reason = bracket["stop"], "stop"
            elif bracket["take_profit"] is not None and current >= bracket["take_profit"]:
                exit_px, reason = bracket["take_profit"], "target"
            if exit_px is None:
                continue
            qty = bracket["qty"]
            proceeds = qty * exit_px
            fee = qty * self.commission_per_share
            self.cash += proceeds - fee
            held = self.equity_positions.get(bracket["symbol"], Decimal("0"))
            if held < qty:
                raise PolicyViolation("Bracket exit exceeds simulated holding.")
            self.equity_positions[bracket["symbol"]] = held - qty
            bracket["status"] = reason
            self._fills.setdefault(order_id, []).append(Fill(
                fill_id=f"sim-fill-{uuid.uuid4()}", quantity=-qty,
                price=exit_px, fee=fee, filled_at=_now()))
            events.append({"order_id": order_id, "symbol": bracket["symbol"],
                           "reason": reason, "price": str(exit_px)})
        return events

    # -- legacy backend interface (lets the simulator run the real ----------------
    # -- service + ledger path in tests and simulation seasons) -----------------
    def review_equity_order(self, request: EquityOrderRequest) -> OrderReview:
        return self.review_order(request)

    def place_equity_order(self, request: EquityOrderRequest, review_id: str | None) -> Order:
        return self.place_order(request, review_id)

    def get_equity_order(self, order_id: str) -> Order:
        try:
            return self._by_id[order_id].order
        except KeyError as exc:
            raise PolicyViolation("Unknown simulation order.") from exc

    def cancel_equity_order(self, order_id: str) -> CancelResult:
        return self.cancel_order(order_id)

    def settle_expired(self, today: str) -> list[dict]:
        """Cash-settle long options at/after expiry; worthless when OTM."""
        from datetime import date as _date

        try:
            today_d = _date.fromisoformat(today)
        except ValueError as exc:
            raise PolicyViolation("settle_expired requires YYYY-MM-DD.") from exc
        settled = []
        for sym, pos in list(self.option_positions.items()):
            quote = self.option_quotes.get(sym)
            if quote is None:
                raise PolicyViolation(f"No quote for {sym}; refusing to settle blind.")
            if today_d < _date.fromisoformat(pos.expiry):
                continue
            intrinsic = intrinsic_value(quote.contract, quote.underlying_price)
            proceeds = Decimal(pos.quantity) * intrinsic * CONTRACT_MULTIPLIER
            buy_cost = Decimal(pos.quantity) * pos.avg_premium * CONTRACT_MULTIPLIER
            buy_fee = Decimal(pos.quantity) * self.fee_per_contract
            self.cash += proceeds
            pnl = proceeds - buy_cost - buy_fee
            del self.option_positions[sym]
            settled.append({"option_symbol": sym, "intrinsic": str(intrinsic),
                            "proceeds": str(proceeds), "realized_pnl": str(pnl)})
        return settled

    # -- helpers ---------------------------------------------------------
    def _mark(self, symbol: str) -> Decimal:
        try:
            return self.equity_marks[symbol.upper()]
        except KeyError as exc:
            raise PolicyViolation(f"No simulation mark for {symbol}.") from exc

    def _check_range(self, symbol: str, price: Decimal) -> None:
        bounds = self.day_ranges.get(symbol.upper())
        if bounds and not bounds[0] <= price <= bounds[1]:
            raise PolicyViolation(f"Fill {price} outside {symbol} day range; refusing.")

    def _single_long_leg(self, request: OptionOrderRequest):
        legs = list(request.legs)
        if len(legs) != 1:
            raise PolicyViolation("Simulation supports single-leg long options only.")
        leg = legs[0]
        if request.order_type != "limit":
            raise PolicyViolation("Simulation option orders must be limit.")
        if leg.side == "buy" and leg.effect != "open":
            raise PolicyViolation("Simulation buys must open a long position.")
        if leg.side == "sell" and leg.effect != "close":
            raise PolicyViolation("Simulation sells must close a long position.")
        if leg.side not in {"buy", "sell"}:
            raise PolicyViolation("Leg side must be buy or sell.")
        quote = self.get_option_quote(leg.symbol)
        contract = quote.contract
        if leg.option_type != contract.right:
            raise PolicyViolation("Leg type disagrees with the chain contract.")
        if leg.strike_price != contract.strike or leg.expiration_date != contract.expiry:
            raise PolicyViolation("Leg strike/expiry disagree with the chain contract.")
        return leg

    def _record(self, request, review_id: str, order: Order, qty: Decimal,
                price: Decimal, fee: Decimal) -> Order:
        sim = _SimOrder(order, review_id, _fingerprint(request), request,
                        filled_qty=qty, status="filled"
                        if order.status == "filled" else "queued")
        self._orders[review_id] = sim
        self._by_id[order.id] = sim
        if order.status == "filled":
            self._fills.setdefault(order.id, []).append(Fill(
                fill_id=f"sim-fill-{uuid.uuid4()}", quantity=qty,
                price=price, fee=fee, filled_at=_now()))
        return order

    def _filled(self, request, review_id, qty, price, fee) -> Order:
        order = Order(f"sim-order-{uuid.uuid4()}", request.account_id,
                      request.symbol.upper(), request.side, "filled",
                      raw={"sim": True, "review_id": review_id})
        return self._record(request, review_id, order, qty, price, fee)

    def _resting(self, request, review_id, qty) -> Order:
        order = Order(f"sim-order-{uuid.uuid4()}", request.account_id,
                      request.symbol.upper(), request.side, "queued",
                      raw={"sim": True, "review_id": review_id})
        return self._record(request, review_id, order, qty, Decimal("0"), Decimal("0"))

    def _filled_option(self, request, review_id, leg, price, fee) -> Order:
        order = Order(f"sim-order-{uuid.uuid4()}", "sim-1", leg.symbol,
                      leg.side, "filled", raw={"sim": True, "review_id": review_id})
        return self._record(request, review_id, order, Decimal(leg.quantity), price, fee)

    def _resting_option(self, request, review_id, leg) -> Order:
        order = Order(f"sim-order-{uuid.uuid4()}", "sim-1", leg.symbol,
                      leg.side, "queued", raw={"sim": True, "review_id": review_id})
        return self._record(request, review_id, order, Decimal(leg.quantity),
                            Decimal("0"), Decimal("0"))
