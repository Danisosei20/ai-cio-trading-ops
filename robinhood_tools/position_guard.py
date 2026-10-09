"""Position guard: deterministic stop/target evaluation for paper positions.

Pure logic, no broker. Given entry price, current price, and a policy,
returns HOLD, STOP, or TARGET. Stops protect capital; targets lock gains;
anything else is noise the desk ignores.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .errors import PolicyViolation

Signal = Literal["HOLD", "STOP", "TARGET"]


@dataclass(frozen=True)
class GuardPolicy:
    stop_pct: Decimal = Decimal("0.08")    # sell when down >= 8%
    target_pct: Decimal = Decimal("0.20")  # sell when up >= 20%
    trailing: bool = True  # ratchet the stop up under winners, never down

    def __post_init__(self) -> None:
        if not Decimal("0") < self.stop_pct < 1:
            raise PolicyViolation("stop_pct must be between 0 and 1.")
        if not Decimal("0") < self.target_pct < 1:
            raise PolicyViolation("target_pct must be between 0 and 1.")


def trail_stop(entry: Decimal, peak: Decimal, policy: GuardPolicy) -> Decimal:
    """Effective stop: fixed floor, ratcheted up by the peak when trailing."""
    floor = entry * (1 - policy.stop_pct)
    if not policy.trailing:
        return floor
    return max(floor, peak * (1 - policy.stop_pct))


@dataclass(frozen=True)
class GuardEvaluation:
    signal: Signal
    return_pct: Decimal
    stop_price: Decimal
    target_price: Decimal


def evaluate(price: Decimal, entry: Decimal, policy: GuardPolicy,
             peak: Decimal | None = None) -> GuardEvaluation:
    if entry <= 0 or price <= 0:
        raise PolicyViolation("Prices must be positive.")
    stop_price = trail_stop(entry, peak if peak is not None else price, policy)
    target_price = entry * (1 + policy.target_pct)
    ret = price / entry - 1
    if price <= stop_price:
        signal: Signal = "STOP"
    elif price >= target_price:
        signal = "TARGET"
    else:
        signal = "HOLD"
    return GuardEvaluation(signal, ret, stop_price, target_price)
