"""Long-option paper order flow (owner-authorized 2026-10-07, paper only).

Mirrors place_signal_order for single-leg long openers:
mode/kill/state → universe (underlying) → earnings blackout → premium cap
→ chain-quality + selection filters → broker review → ledger → guarded place.

Sells-to-close travel the same path (side sell, effect close).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .alpaca_options import AlpacaOptionsData
from .database import CioDatabase
from .errors import PolicyViolation
from .models import OptionLeg, OptionOrderRequest
from .options import OptionSelectionFilters, select_long_option
from .options_gate import DEFAULT_MIN_EV_PCT
from .safety import require_earnings_clear
from .universe import Sp500Snapshot

WEEKEND_GAP_CUTOFF_ET = "15:45"   # owner decision 2026-11-16
WEEKEND_GAP_WEEKDAY = 3           # Thursday (Monday = 0)


def weekend_gap_block(*, right: str, when) -> str | None:
    """Owner decision 2026-11-16: no new short premium after Thursday close.

    On 2026-11-10 SPY gapped straight through every -15% put stop and the
    desk lost the whole short book: a put opened Thursday PM cannot be
    exited until Monday's open, so an overnight stop is fiction across
    that window. Long (call) openers are unaffected - the rule mirrors
    the loss that was actually observed.
    """
    if str(right).lower() != "put":
        return None
    if when.weekday() != WEEKEND_GAP_WEEKDAY:
        return None
    cutoff = datetime.strptime(WEEKEND_GAP_CUTOFF_ET, "%H:%M").time()
    if when.time() >= cutoff:
        return (f"weekend gap rule: no new put openers after Thursday "
                f"{WEEKEND_GAP_CUTOFF_ET} ET (unexitable until Monday open)")
    return None


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
                      override_reason: str = "",
                       odds_gate: bool = True,
                       odds_sigma: float | None = None,
                       min_ev_pct: float = DEFAULT_MIN_EV_PCT) -> dict:
    """Review → ledger → guarded placement of one long option opener."""
    from .runtime import build_paper_options_service as _build

    if contracts < 1:
        raise PolicyViolation("At least one contract is required.")
    today = today or date.today()
    now_et = datetime.now(ZoneInfo("America/New_York"))

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
    # Weekend gap rule (owner decision 2026-11-16): Thursdays after 15:45
    # ET open no new short premium - the position cannot be exited until
    # Monday's open, so a -15% stop is fiction across the weekend.
    gap_block = weekend_gap_block(right=contract.right, when=now_et)
    if gap_block:
        raise PolicyViolation(gap_block)
    # Per-symbol loss memory on entries: an underlier that keeps losing
    # rests before it can be bought again (RDDT, TTWO, 2026-11-10/16).
    # Entry-only by construction: this function places openers, and the
    # sell-to-close path lives in close_long_option, untouched.
    db.require_no_symbol_loss_streak(symbol, today=today.isoformat())
    snapshot.require_eligible_purchase(symbol)
    # Odds gate (owner decision 2026-11-16): the contract's simulated EV
    # under the guard's exit bands must be positive before it can be
    # opened - the equity path has gated on the Time Machine all along,
    # and the options book is where the desk's losses are. Fail-closed:
    # no vol estimate means no odds, and no odds means no order. Exits
    # are never gated (close_long_option does not call this).
    if odds_gate:
        from .options_gate import require_positive_ev

        require_positive_ev(quote=quote, today=today,
                            sigma=odds_sigma,
                            min_ev_pct=min_ev_pct)
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
    result = {"action": "buy_placed", "contract": contract.option_symbol,
              "qty": contracts, "limit": str(request.limit_price),
              "premium": str(premium), "order_id": placed.id,
              "status": placed.status, "approval_id": auth.approval_id}
    # One Slack message per placed order (owner directive 2026-11-16). The
    # end-of-run summary is not a substitute: 13 entries logged today with
    # no per-order alert. Never blocks the order path.
    #
    # The outcome is recorded, not discarded. On 2026-11-17 an order reached
    # the broker with no ledger trace and no alert, and after the fact there
    # was no way to tell whether the alert had fired or failed - the return
    # value was thrown away. 'sent'/'failed' now travels in the result dict
    # and out through the caller's log.
    alert = "failed"
    try:
        from .order_alerts import is_simulation_service, option_message, post_order_alert

        alert = post_order_alert(
            settings=settings,
            simulated=is_simulation_service(service),
            message=option_message(side="buy", contract=contract.option_symbol,
                                   qty=contracts, limit=request.limit_price,
                                   premium=premium, order_id=placed.id,
                                   approval_id=auth.approval_id))
    except Exception:  # noqa: BLE001 - alerting is best-effort, never fatal
        alert = "failed"
    result["alert"] = alert
    return result


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
            held += abs(Decimal(str(position.get("qty", "0"))))
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
    # One Slack message per placed exit (owner directive 2026-11-16): the
    # guard closes positions all session and the operator only learned about
    # it from the next run summary. The outcome is recorded, not discarded -
    # see the note in place_long_option for why that matters.
    alert = "failed"
    try:
        from .order_alerts import is_simulation_service, option_message, post_order_alert

        alert = post_order_alert(
            settings=settings,
            simulated=is_simulation_service(service),
            message=option_message(side="sell", contract=contract.option_symbol,
                                   qty=contracts, limit=request.limit_price,
                                   order_id=placed.id,
                                   approval_id=auth.approval_id))
    except Exception:  # noqa: BLE001 - alerting is best-effort, never fatal
        alert = "failed"
    result["alert"] = alert
    return result
