#!/usr/bin/env python3
"""Compatible SPY regime classification across staged and clean checkouts.

The committed ``market_regime(spy_closes)`` has no ``require_long_term``
parameter, while its committed caller passes that keyword. A bare
``except Exception`` in the research script turns the resulting ``TypeError``
into ``UNKNOWN``, which silently blocks all new risk in a clean checkout.

This module is a *compatibility shim*: it does not modify the staged
``market_prices.py`` and it does not change any regime threshold. It supports
both signatures and never converts a programming error into a regime value.
"""
from __future__ import annotations

import inspect
from typing import Callable


def _base_market_regime() -> Callable[..., str]:
    from . import market_prices
    return market_prices.market_regime


def supports_long_term() -> bool:
    """Does the installed market_regime accept the require_long_term flag?"""
    try:
        params = inspect.signature(_base_market_regime()).parameters
    except (TypeError, ValueError):
        return False
    return "require_long_term" in params


def market_regime_compatible(spy_closes: list[float], *,
                             require_long_term: bool = False) -> str:
    """Classify SPY with optional 50/200-day confirmation, both signatures.

    Missing or stale evidence remains fail-closed (``UNKNOWN``). A genuine
    programming error is raised, never swallowed into a regime string.
    """
    closes = list(spy_closes) if spy_closes is not None else []
    fn = _base_market_regime()
    if supports_long_term():
        return fn(closes, require_long_term=require_long_term)
    # Legacy signature: reproduce the documented strict semantics locally.
    # The legacy 50-day rule itself is owned by market_regime, not copied.
    if len(closes) < 51:
        return "UNKNOWN"
    if require_long_term and len(closes) < 201:
        return "UNKNOWN"
    base = fn(closes)
    if not require_long_term or base != "RISK_ON":
        return base
    long_mean = sum(closes[-201:-1]) / 200
    latest = closes[-1]
    if latest > long_mean:
        return "RISK_ON"
    return "CHOP" if latest == long_mean else "RISK_OFF"


def regime_for_options(spy_closes: list[float], *, options_mode: bool) -> str:
    """Stricter confirmation for options entries; 50-day rule for equities."""
    return market_regime_compatible(spy_closes, require_long_term=options_mode)
