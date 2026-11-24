#!/usr/bin/env python3
"""Verify that a close/reduce order cannot increase or reverse exposure."""
from __future__ import annotations

from decimal import Decimal


def validate_close_order(order, positions: dict | None,
                         pending: dict[str, float] | None = None) -> tuple[bool, str]:
    """Compare requested close quantity with broker-reconciled owned exposure.

    ``positions`` maps normalized symbol to ``{qty, direction, instrument}``.
    When it is unavailable, fail closed (callers may use a reduce-only broker
    order for emergency liquidation through a separate verified path).
    """
    effect = str(getattr(order, "position_effect", "") or "").lower()
    if effect not in ("reduce", "close"):
        return False, f"not a closing order: {effect!r}"
    if positions is None:
        return False, "broker positions unavailable"
    symbol = str(getattr(order, "symbol", "")).strip().upper()
    owned = positions.get(symbol)
    if not isinstance(owned, dict):
        return False, f"no verified position for {symbol}"
    direction = str(owned.get("direction", "")).lower()
    expected_side = "sell" if direction == "long" else "buy" if direction == "short" else ""
    if not expected_side or str(getattr(order, "side", "")) != expected_side:
        return False, f"close side does not reduce {direction or 'unknown'} position"
    instrument = str(owned.get("instrument") or symbol).strip().upper()
    if instrument != symbol:
        return False, f"instrument mismatch: {instrument} != {symbol}"
    try:
        available = Decimal(str(owned.get("qty", 0)))
        requested = Decimal(str(getattr(order, "quantity", 0)))
        reserved = Decimal(str((pending or {}).get(symbol, 0)))
    except Exception:  # noqa: BLE001
        return False, "invalid quantity"
    if requested <= 0 or available <= 0:
        return False, "no owned quantity to close"
    if requested > available - reserved:
        return False, f"close qty {requested} exceeds available {available - reserved}"
    return True, "verified close"
