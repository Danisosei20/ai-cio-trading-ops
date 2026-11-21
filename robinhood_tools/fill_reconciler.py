"""Broker-fill reconciliation: the desk's ledger must prove what filled.

The hole this closes (measured 2026-11-16): the desk database holds 49
approvals and **zero** order_fills and zero trade_lifecycles, while
`reconcile_order()` sat in reconciliation.py with tests and no production
caller. Every fill lived only at the broker, so "what did the desk
actually do today" was answerable only by hand-querying Alpaca - which is
how the 13-entry claim in the 10:23 summary was finally checked (the
broker held 1 order, not 13).

This module pulls the broker's fill activity into `order_fills`, drives
`trade_lifecycles` from observed fills (buy-pending -> open -> sell-
pending -> closed with realized profit), and reports anything the ledger
never authorized (orphan orders, exits with no lifecycle) instead of
silently absorbing it.

Idempotent by construction: `record_fill` is INSERT OR IGNORE, and
lifecycle transitions only fire on the state that expects them, so a
second run over the same broker state records nothing and changes
nothing. It never raises into the caller: a reconciliation run that
cannot complete is reported, not thrown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from .database import CioDatabase
from .errors import PolicyViolation
from .reconciliation import Fill, reconcile_order

OCC = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
FILLED_STATES = {"filled", "partially_filled"}

# Desk-placed orders carry a client_order_id that identifies their origin:
# cio-/cio-opt- (gated equity/option flow) or TICKER:date:setup (day/overnight).
# Anything else (e.g. an Alpaca-generated UUID) came from another bot/client.
_DESK_CLIENT_PREFIXES = ("cio-",)
_DESK_CLIENT_SEPARATOR = ":"


def _is_external_order(order: Mapping) -> bool:
    """True when the client id proves the order did not come from this desk."""
    client_id = str(order.get("client_order_id") or "")
    if not client_id:
        return False  # unknown origin: stay an orphan, never assume external
    if client_id.startswith(_DESK_CLIENT_PREFIXES):
        return False
    if _DESK_CLIENT_SEPARATOR in client_id:
        return False
    return True


def option_multiplier(symbol: str) -> Decimal:
    """Contracts are quoted per share; an option position is 100x that."""
    return Decimal("100") if OCC.match(symbol.upper()) else Decimal("1")


def _field(fill, key: str):
    """Fills arrive two ways: broker Fill dataclasses (this run) and dict
    rows (order_fills from the ledger). Read both."""
    if isinstance(fill, Mapping):
        return fill[key]
    return getattr(fill, key)


def _avg(fills: Iterable) -> tuple[Decimal, Decimal]:
    """(quantity, volume-weighted average price) of a fill list."""
    qty = sum((Decimal(str(_field(f, "quantity"))) for f in fills), Decimal("0"))
    value = sum((Decimal(str(_field(f, "quantity"))) * Decimal(str(_field(f, "price")))
                 for f in fills), Decimal("0"))
    return qty, (value / qty if qty else Decimal("0"))


@dataclass
class ReconcileReport:
    status: str = "ok"
    generated_at: str = ""
    orders_at_broker: int = 0
    filled_orders: int = 0
    fills_recorded: int = 0
    fills_already_known: int = 0
    lifecycles_opened: int = 0
    lifecycles_closed: int = 0
    unattributed_exits: list[str] = field(default_factory=list)
    orphan_orders: list[str] = field(default_factory=list)
    historical_orders: list[str] = field(default_factory=list)
    external_orders: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "generated_at": self.generated_at,
            "orders_at_broker": self.orders_at_broker,
            "filled_orders": self.filled_orders,
            "fills_recorded": self.fills_recorded,
            "fills_already_known": self.fills_already_known,
            "lifecycles_opened": self.lifecycles_opened,
            "lifecycles_closed": self.lifecycles_closed,
            "unattributed_exits": self.unattributed_exits,
            "orphan_orders": self.orphan_orders,
            "historical_orders": self.historical_orders,
            "external_orders": self.external_orders,
            "errors": self.errors,
        }


def reconcile_desk_fills(*, database: CioDatabase,
                         orders: Sequence[Mapping],
                         fills_by_order: Mapping[str, Sequence[Fill]],
                         approvals_by_order: Mapping[str, Mapping] | None = None,
                         historical_order_ids: set[str] | None = None,
                         now_iso: str = "") -> ReconcileReport:
    """Record broker fills into the desk ledger and drive lifecycles.

    `orders` are broker order records (dicts with id/symbol/side/status/
    qty fields), `fills_by_order` maps order id -> Fill objects pulled from
    the broker's fill activity, `approvals_by_order` maps broker order id
    -> the desk's approval row (for lifecycle attribution). Pure over its
    inputs: no network, no clock except the injected timestamp.
    """
    report = ReconcileReport(generated_at=now_iso)
    approvals_by_order = approvals_by_order or {}
    historical_order_ids = historical_order_ids or set()
    try:
        known = database.known_order_ids()
    except Exception as exc:  # noqa: BLE001 - report, never throw
        report.status = "failed"
        report.errors.append(f"known_order_ids unreadable: {type(exc).__name__}: {exc}")
        return report

    for order in orders:
        order_id = str(order.get("id", ""))
        symbol = str(order.get("symbol", "")).upper()
        side = str(order.get("side", "")).lower()
        status = str(order.get("status", "")).lower()
        if not order_id or not symbol:
            continue
        report.orders_at_broker += 1
        if order_id not in known:
            if order_id in historical_order_ids:
                report.historical_orders.append(f"{symbol}:{order_id}")
            elif _is_external_order(order):
                report.external_orders.append(f"{symbol}:{order_id}")
            else:
                report.orphan_orders.append(f"{symbol}:{order_id}")
        if status not in FILLED_STATES:
            continue
        report.filled_orders += 1
        fills = list(fills_by_order.get(order_id, []))
        if not fills:
            continue

        # 1. Fills into order_fills (idempotent).
        try:
            shim = _OrderShim(order_id=order_id, symbol=symbol, side=side,
                              status=status)
            result = reconcile_order(database, shim, fills)  # type: ignore[arg-type]
            report.fills_recorded += result.new_fills
            report.fills_already_known += max(len(fills) - result.new_fills, 0)
        except Exception as exc:  # noqa: BLE001 - one bad order never stops the run
            report.errors.append(f"fills {order_id}: {type(exc).__name__}: {exc}")
            continue

        # 2. Lifecycle from the observed fill.
        try:
            if side == "buy":
                _drive_buy(database, symbol, order_id,
                           approvals_by_order.get(order_id, {}), report)
            else:
                _drive_sell(database, symbol, order_id, fills, report)
        except PolicyViolation as exc:
            # A lifecycle that refuses a transition is a ledger fact worth
            # recording, not a run-stopper.
            report.errors.append(f"lifecycle {symbol}: {exc}")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"lifecycle {symbol}: {type(exc).__name__}: {exc}")

    report.orphan_orders.sort()
    report.historical_orders.sort()
    report.external_orders.sort()
    report.unattributed_exits.sort()
    return report


@dataclass
class _OrderShim:
    """reconcile_order() reads id/symbol/status/side; the broker dict has them.

    The shim keeps the reconciler off the typed Order path so one
    reconciliation run costs one activities call, not one get_order call
    per broker order.
    """

    order_id: str
    symbol: str
    side: str
    status: str = "filled"

    @property
    def id(self) -> str:
        return self.order_id


def _drive_buy(database: CioDatabase, symbol: str, order_id: str,
               approval: Mapping, report: ReconcileReport) -> None:
    """Buy fill -> lifecycle exists and the position is open.

    A fill is stronger evidence than an approval row: the desk holds the
    position whether or not the ledger saw the authorization, so the
    reconciler drives research/buy_pending -> open directly rather than
    leaving the lifecycle stuck without one.
    """
    approval_id = str(approval.get("approval_id", "")) or None
    try:
        lifecycle = database.get_trade_lifecycle(symbol)
    except PolicyViolation:
        database.begin_trade_lifecycle(symbol, buy_approval_id=approval_id)
        report.lifecycles_opened += 1
        lifecycle = database.get_trade_lifecycle(symbol)
    if lifecycle["status"] in ("research", "buy_pending"):
        try:
            database.open_lifecycle_from_fill(symbol, order_id=order_id)
        except PolicyViolation:
            # Already open/closed, or a transition the state machine
            # refuses: the fill is recorded either way.
            pass


def _drive_sell(database: CioDatabase, symbol: str, order_id: str,
                sell_fills: Sequence[Fill], report: ReconcileReport) -> None:
    """Sell fill -> open lifecycle closes with realized profit."""
    try:
        lifecycle = database.get_trade_lifecycle(symbol)
    except PolicyViolation:
        # A close with no open lifecycle: the ledger never saw the entry
        # (or it predates this ledger). Report it; do not invent a row.
        report.unattributed_exits.append(f"{symbol}:{order_id}")
        return
    if lifecycle["status"] == "closed":
        return
    if lifecycle["status"] == "research":
        report.unattributed_exits.append(f"{symbol}:{order_id}")
        return
    if lifecycle["status"] == "buy_pending":
        report.unattributed_exits.append(f"{symbol}:{order_id}")
        return
    if lifecycle["status"] == "open":
        database.mark_sell_pending(symbol, approval_id=None)
    realized = _realized_profit(database, lifecycle, sell_fills, symbol)
    database.close_trade_lifecycle(symbol, order_id=order_id,
                                   realized_profit=str(realized))
    report.lifecycles_closed += 1


def _realized_profit(database: CioDatabase, lifecycle: Mapping,
                     sell_fills: Sequence[Fill], symbol: str) -> Decimal:
    """(sell avg - buy avg) x closed qty, contracts at their multiplier.

    Buy fills come from the ledger first (persisted by earlier runs), then
    from this run's activity pull. Options are 100x per contract.
    """
    mult = option_multiplier(symbol)
    sell_qty, sell_avg = _avg(sell_fills)
    buy_fills: list[Mapping] = []
    buy_order_id = lifecycle.get("buy_order_id")
    if buy_order_id:
        buy_fills = database.order_fills(str(buy_order_id))
    if not buy_fills:
        return Decimal("0")
    buy_qty, buy_avg = _avg(buy_fills)
    closed = min(buy_qty, sell_qty)
    return ((sell_avg - buy_avg) * closed * mult).quantize(Decimal("0.01"))