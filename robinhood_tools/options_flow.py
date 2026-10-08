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
                      service=None, today: date | None = None) -> dict:
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
    require_earnings_clear(today=today, earnings_date=earnings_date,
                           blackout_days=settings.earnings_blackout_days)

    premium = Decimal(contracts) * quote.ask * 100
    if premium > premium_cap:
        raise PolicyViolation(f"Premium ~${premium} exceeds ${premium_cap} cap.")
    if service is None:
        service = _build(settings=settings, sp500_snapshot=snapshot
                         or Sp500Snapshot(symbols=frozenset(),
                                          as_of="2000-01-01T00:00:00+00:00",
                                          source_url="https://example.com/unused"),
                         env_path=".env")
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
    placed = service.place_option_order(request, review_id=review.review_id,
                                        approval_id=auth.approval_id, confirmed=False)
    return {"action": "buy_placed", "contract": contract.option_symbol,
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
    return {"action": "sell_placed", "contract": contract.option_symbol,
            "qty": contracts, "limit": str(request.limit_price),
            "order_id": placed.id, "status": placed.status,
            "approval_id": auth.approval_id}
