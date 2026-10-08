"""Daily profit target: pure math, no broker. Tested."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class DayStatus:
    pnl: Decimal
    target: Decimal
    max_loss: Decimal
    halted: bool
    reason: str = ""


def day_pnl(first_equity: Decimal, last_equity: Decimal) -> Decimal:
    return last_equity - first_equity


def evaluate(pnl: Decimal, target: Decimal = Decimal("100"),
             max_loss: Decimal = Decimal("50")) -> DayStatus:
    """Halt new risk at +target (bank the day) or past -max_loss (stop the bleed)."""
    if pnl >= target:
        return DayStatus(pnl, target, max_loss, True, f"target +${target} reached")
    if pnl <= -max_loss:
        return DayStatus(pnl, target, max_loss, True, f"loss limit -${max_loss} hit")
    return DayStatus(pnl, target, max_loss, False)
