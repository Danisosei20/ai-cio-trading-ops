"""Long-option paper order flow (owner-authorized 2026-10-07, paper only).

Mirrors place_signal_order for single-leg long openers:
mode/kill/state → universe (underlying) → earnings blackout → premium cap
→ chain-quality + selection filters → broker review → ledger → guarded place.

Sells-to-close travel the same path (side sell, effect close).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .alpaca_options import AlpacaOptionsData
from .database import CioDatabase
from .errors import PolicyViolation
from .models import OptionLeg, OptionOrderRequest
from .options import OptionSelectionFilters, select_long_option
from .safety import require_earnings_clear
from .universe import Sp500Snapshot


def pick_long_option(*, data: AlpacaOptionsData, underlying: str, spot: Decimal,
                     direction: str, today: date,
                     filters: OptionSelectionFilters | None = None):
    """Scout + select. Returns (quote, rejections). No orders."""
    right = "call" if direction == "bullish" else "put"
    if direction not in {"bullish", "bearish"}:
        raise PolicyViolation("Direction must be bullish or bearish.")
    quotes = data.quote_chain(underlying, spot, today, right)
    return select_long_option(quotes, direction=direction, today=today.isoformat(),
                              filters=filters)


def place_long_option(*, settings, snapshot: Sp500Snapshot | None,
                      quote, contracts: int,
                      earnings_date: date | None, premium_cap: Decimal,
                      database: CioDatabase | None = None,
                      service=None, today: date | None = None,
                      session_override: tuple[str, str] | None = None,
                      override_reason: str = "") -> dict:
    """Review → ledger → guarded placement of one long option opener."""
    from .runtime import build_paper_options_service as _build

    if contracts < 1:
        raise PolicyViolation("At least one contract is required.")
    today = today or date.today()

    settings.require_paper_trading()
    if settings.trading_enabled:
        raise PolicyViolation("TRADING_ENABLED=true; paper flow refuses.")
    if not settings.paper_trading_enabled or not settings.paper_autonomy.enabled:
        raise PolicyViolation("Paper trading or autonomy is disabled.")

    db = database or CioDatabase(settings.database_path)
    db.require_not_killed()
    if snapshot is not None and settings.index_etf_allowlist:
        import dataclasses as _dc

        snapshot = _dc.replace(snapshot, index_etfs=frozenset(settings.index_etf_allowlist))

    contract = quote.contract
    symbol = contract.underlying
    if snapshot is None:
        raise PolicyViolation("No membership evidence; option openers blocked.")
    snapshot.require_eligible_purchase(symbol)
    # Index ETFs have no single earnings event; the blackout cannot apply.
    if symbol not in {item.upper() for item in snapshot.index_etfs}:
        require_earnings_clear(today=today, earnings_date=earnings_date,
                               blackout_days=settings.earnings_blackout_days)

    premium = Decimal(contracts) * quote.ask * 100
    if premium > premium_cap:
        raise PolicyViolation(f"Premium ~${premium} exceeds ${premium_cap} cap.")
    extra: dict = {}
    if session_override is not None:
        if not override_reason:
            raise PolicyViolation("Session overrides require a recorded reason.")
        extra = {"earliest_entry_et": session_override[0],
                 "latest_entry_et": session_override[1]}
    if service is None:
        service = _build(settings=settings, sp500_snapshot=snapshot
                         or Sp500Snapshot(symbols=frozenset(),
                                          as_of="2000-01-01T00:00:00+00:00",
                                          source_url="https://example.com/unused"),
                         env_path=".env", **extra)
    service.approval_store = db
    leg = OptionLeg(symbol=contract.option_symbol, side="buy", effect="open",
                    option_type=contract.right, expiration_date=contract.expiry,
                    strike_price=contract.strike, quantity=contracts)
    request = OptionOrderRequest(account_id=service.backend.list_accounts()[0].id,
                                 legs=(leg,), order_type="limit",
                                 time_in_force="gfd",
                                 limit_price=quote.ask.quantize(Decimal("0.01")))
    review = service.review_option_order(request)
    auth = db.create_option_approval(request, review,
                                     window_minutes=settings.approval_window_minutes)
    db.approve(auth.approval_id)
    if session_override is not None:
        db.audit("session_override",
                 {"window": list(session_override), "reason": override_reason,
                  "contract": contract.option_symbol},
                 correlation_id=auth.approval_id, approval_id=auth.approval_id)
    placed = service.place_option_order(request, review_id=review.review_id,
                                        approval_id=auth.approval_id, confirmed=False)
    return {"action": "buy_placed", "accepted": True, "contract": contract.option_symbol,
            "qty": contracts, "limit": str(request.limit_price),
            "premium": str(premium), "order_id": placed.id,
            "status": placed.status, "approval_id": auth.approval_id}


def close_long_option(*, settings, quote, contracts: int,
                      database=None, service=None,
                      today: date | None = None) -> dict:
    """Sell-to-close a long position through review + ledger + guard."""
    from .runtime import build_paper_options_service as _build

    if contracts < 1:
        raise PolicyViolation("At least one contract is required.")
    today = today or date.today()

    settings.require_paper_trading()
    if settings.trading_enabled:
        raise PolicyViolation("TRADING_ENABLED=true; paper flow refuses.")

    db = database or CioDatabase(settings.database_path)
    db.require_not_killed()

    contract = quote.contract
    if service is None:
        service = _build(settings=settings, sp500_snapshot=None,
                         env_path=".env")
    service.approval_store = db
    leg = OptionLeg(symbol=contract.option_symbol, side="sell", effect="close",
                    option_type=contract.right, expiration_date=contract.expiry,
                    strike_price=contract.strike, quantity=contracts)
    if quote.bid is None or quote.bid <= 0:
        raise PolicyViolation("No bid to sell into; close refused.")
    held = Decimal("0")
    lister = getattr(service.backend, "list_positions", None) or getattr(
        service.backend, "get_positions", None)
    if lister is None:
        raise PolicyViolation("Backend cannot list positions; close refused.")
    for position in lister():
        if str(position.get("symbol", "")).upper() == contract.option_symbol:
            quantity = Decimal(str(position.get("qty", "0")))
            if quantity < 0:
                raise PolicyViolation("Negative exact OCC quantity is invalid for sell-to-close.")
            held += quantity
    if held < contracts:
        raise PolicyViolation(
            f"Holdings check failed: {held} held vs {contracts} to close; "
            f"naked short refused.")
    request = OptionOrderRequest(account_id=service.backend.list_accounts()[0].id,
                                 legs=(leg,), order_type="limit",
                                 time_in_force="gfd",
                                 limit_price=quote.bid.quantize(Decimal("0.01")))
    review = service.review_option_order(request)
    auth = db.create_option_approval(request, review,
                                     window_minutes=settings.approval_window_minutes)
    db.approve(auth.approval_id)
    placed = service.place_option_order(request, review_id=review.review_id,
                                        approval_id=auth.approval_id, confirmed=False)
    result = {"action": "sell_placed", "contract": contract.option_symbol,
             "qty": contracts, "limit": str(request.limit_price),
             "order_id": placed.id, "status": placed.status,
             "approval_id": auth.approval_id}

    def require_reconciliation(reason: str, *, broker_status=None,
                               positions=None, error: str | None = None) -> dict:
        """Record an unresolved close observation without releasing its claim."""
        metadata = {
            "required": True,
            "reason": reason,
            "order_id": placed.id,
            "broker_status": str(broker_status if broker_status is not None else placed.status),
            "position_evidence": positions,
            "claim_released": False,
        }
        if error:
            metadata["error"] = error
        # The approval is already terminal/acknowledged at this point, so the
        # audit event is the durable reconciliation marker.  In particular, do
        # not turn a bad position observation into a closed OCC claim.
        db.audit("reconciliation_required", metadata,
                 correlation_id=auth.approval_id, approval_id=auth.approval_id)
        result.update({"reconciliation_required": True, "reconciliation": metadata})
        return result
    # A placement response is only an acknowledgement. Re-read the order from
    # the broker before using it as closure evidence.
    try:
        get_order = getattr(service.backend, "get_option_order", None)
        if get_order is None:
            raise PolicyViolation("Backend cannot retrieve option order status; close reconciliation required.")
        authoritative = get_order(placed.id)
        terminal = str(authoritative.status).lower() in {"filled", "closed", "executed"}
        position_check = lister()
    except Exception as exc:
        return require_reconciliation(
            "unable to obtain terminal order and fresh position evidence",
            error=str(exc) or type(exc).__name__,
        )
    if terminal:
        from .paper_autopilot import reconcile_candidate_claim
        claim_closed = reconcile_candidate_claim(
            db.path if hasattr(db, "path") else settings.database_path,
            contract.option_symbol, order_id=authoritative.id,
            broker_status=authoritative.status, positions=position_check,
        )
        if not claim_closed:
            if not isinstance(position_check, list):
                reason = "fresh position evidence is invalid"
            else:
                try:
                    exact = [item for item in position_check
                             if str(item.get("symbol", "")).upper() == contract.option_symbol]
                    quantities = [Decimal(str(item.get("qty", "0"))) for item in exact]
                    invalid = any(not quantity.is_finite() or quantity < 0
                                  for quantity in quantities)
                    reason = ("fresh exact-OCC position evidence is invalid"
                              if invalid else
                              "fresh exact-OCC position is non-flat"
                              if any(quantity != 0 for quantity in quantities)
                              else "fresh exact-OCC position evidence is invalid")
                except (ArithmeticError, AttributeError, TypeError, ValueError):
                    reason = "fresh exact-OCC position evidence is invalid"
            return require_reconciliation(
                reason, broker_status=authoritative.status, positions=position_check,
            )
        result["claim_closed"] = True
    else:
        return require_reconciliation(
            "close order is not terminal", broker_status=authoritative.status,
            positions=position_check,
        )
    return result
