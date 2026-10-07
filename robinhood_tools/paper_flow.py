"""Shared signal-to-paper-order flow (Phase 2 convergence).

Both autonomous entry points (`scripts/tradingagents_paper_auto.py` and
`scripts/alert_webhook.py`) place through `place_signal_order()` so gates
cannot drift between callers. The flow enforces, in order:

1. mode/kill/state guards (paper_auto, kill switch, symbol cooldown)
2. universe gate for buys (S&P500 snapshot supplied by caller)
3. earnings blackout for buys (fail-closed on unknown date)
4. order-value cap
5. broker review (fingerprint-bound review ID, no order)
6. ledger create → approve (durable, expiring)
7. guarded placement (session window + market clock via the paper service,
   atomic reservation, idempotent client key)

Callers keep their own signal-specific prescreens (buying power, holdings,
price sourcing). Full-candidate validation (scores, regime, ADV, portfolio
weights) lives in `PaperAutoExecutor` / `RiskEngine` and applies once
callers can supply that evidence (Phase 3 typed agents).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from .database import CioDatabase
from .errors import PolicyViolation
from .models import EquityOrderRequest
from .runtime import build_paper_service
from .safety import require_earnings_clear
from .universe import Sp500Snapshot


@dataclass(frozen=True)
class SignalOrder:
    symbol: str
    side: str  # "buy" | "sell"
    quantity: Decimal
    limit_price: Decimal
    earnings_date: date | None = None
    max_order_value: Decimal | None = None
    # Bracket exits for buys (entry limit must sit between stop and target).
    take_profit_price: Decimal | None = None
    bracket_stop_price: Decimal | None = None


def place_signal_order(*, settings, snapshot: Sp500Snapshot | None, order: SignalOrder,
                       database: CioDatabase | None = None,
                       env: dict | None = None, env_path: str | Path = ".env",
                       service=None, today: date | None = None) -> dict:
    """Execute one gated paper order. Returns order/approval identifiers."""
    if order.side not in {"buy", "sell"}:
        raise PolicyViolation("Side must be buy or sell.")
    if order.quantity <= 0:
        raise PolicyViolation("Quantity must be positive.")
    if order.limit_price <= 0:
        raise PolicyViolation("Limit price must be positive.")
    today = today or date.today()

    settings.require_paper_trading()
    if settings.trading_enabled:
        raise PolicyViolation("TRADING_ENABLED=true; paper flow refuses.")
    if not settings.paper_trading_enabled or not settings.paper_autonomy.enabled:
        raise PolicyViolation("Paper trading or autonomy is disabled.")

    db = database or CioDatabase(settings.database_path)
    db.require_not_killed()
    db.require_no_symbol_cooldown(order.symbol, today=today.isoformat())
    symbol = order.symbol.upper()
    if order.side == "buy":
        if snapshot is None:
            raise PolicyViolation("No S&P 500 membership evidence; buys blocked.")
        snapshot.require_current_member(symbol)
        require_earnings_clear(today=today, earnings_date=order.earnings_date,
                               blackout_days=settings.earnings_blackout_days)

    cap = (order.max_order_value if order.max_order_value is not None
           else settings.risk_limits.max_order_value)
    cap = min(cap, settings.risk_limits.max_order_value)
    if order.quantity is None or order.limit_price is None:
        raise PolicyViolation("Quantity and limit price are required.")
    if order.quantity * order.limit_price > cap:
        raise PolicyViolation(f"Order value exceeds ${cap} paper cap.")

    if service is None:
        from datetime import datetime, timezone

        from .alpaca_paper import AlpacaPaperHttpTransport

        # Sells never consult membership (service checks buys only); an empty
        # snapshot here cannot authorize anything.
        review_snapshot = snapshot or Sp500Snapshot(
            symbols=frozenset(), as_of=datetime.now(timezone.utc).isoformat(),
            source_url="https://example.com/unused-for-sells")
        transport = None
        if env:
            transport = AlpacaPaperHttpTransport.from_values(env)
        service = build_paper_service(settings=settings, sp500_snapshot=review_snapshot,
                                      env_path=str(env_path), transport=transport)
    # One ledger for create/approve (here) and reserve/execute (service):
    # a split store would let the atomic reservation miss our approval.
    service.approval_store = db
    account_id = service.backend.list_accounts()[0].id

    request = EquityOrderRequest(
        account_id=account_id, symbol=symbol, side=order.side,  # type: ignore[arg-type]
        order_type="limit", time_in_force="gfd", quantity=order.quantity,
        limit_price=order.limit_price.quantize(Decimal("0.01")), extended_hours=False,
        take_profit_price=(order.take_profit_price.quantize(Decimal("0.01"))
                           if order.take_profit_price else None),
        bracket_stop_price=(order.bracket_stop_price.quantize(Decimal("0.01"))
                            if order.bracket_stop_price else None))
    review = service.review_equity_order(request)
    auth = db.create(request, review, window_minutes=settings.approval_window_minutes)
    db.approve(auth.approval_id)
    placed = service.place_equity_order(request, review_id=review.review_id,
                                        approval_id=auth.approval_id, confirmed=False)
    return {"action": f"{order.side}_placed", "symbol": symbol,
            "qty": str(order.quantity), "limit": str(request.limit_price),
            "take_profit": str(request.take_profit_price),
            "bracket_stop": str(request.bracket_stop_price),
            "order_id": placed.id, "status": placed.status,
            "approval_id": auth.approval_id, "review_id": review.review_id}


def lookup_earnings_date(symbol: str) -> date | None:
    """Best-effort earnings date via yfinance; None means unknown.

    Lazy import keeps the core stdlib-only. Callers pass the result into
    SignalOrder; the flow fails closed on None for buys.
    """
    try:
        import yfinance as yf  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return None
    try:
        cal = yf.Ticker(symbol).calendar
        if cal is not None and len(cal):
            val = cal.iloc[0, 0] if hasattr(cal, "iloc") else None
            if val is not None:
                return date.fromisoformat(str(val)[:10])
    except Exception:
        pass
    try:
        ed = yf.Ticker(symbol).earnings_dates
        if ed is not None and len(ed):
            return ed.index[0].date()
    except Exception:
        pass
    return None
