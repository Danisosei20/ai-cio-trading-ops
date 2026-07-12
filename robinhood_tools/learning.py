from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .errors import PolicyViolation


@dataclass(frozen=True)
class OutcomeObservation:
    recommendation_id: str
    symbol: str
    horizon_days: int
    return_pct: Decimal
    benchmark_return_pct: Decimal
    thesis_accurate: bool
    execution_slippage_pct: Decimal
    error_category: str
    strategy_version: str = "v1"
    market_regime: str = "unknown"
    expected_return_pct: Decimal | None = None
    max_favorable_excursion_pct: Decimal | None = None
    max_adverse_excursion_pct: Decimal | None = None

    @property
    def excess_return(self) -> Decimal:
        return self.return_pct - self.benchmark_return_pct


def validate_policy_change(observations: list[OutcomeObservation], *, repeated_error: str, minimum: int = 10) -> dict:
    comparable = [item for item in observations if item.error_category == repeated_error]
    if len(comparable) < minimum:
        raise PolicyViolation(
            f"Policy changes require at least {minimum} comparable observations; found {len(comparable)}."
        )
    average_excess = sum((item.excess_return for item in comparable), Decimal("0")) / len(comparable)
    thesis_accuracy = sum(item.thesis_accurate for item in comparable) / len(comparable)
    return {
        "observations": len(comparable),
        "repeated_error": repeated_error,
        "average_excess_return": average_excess,
        "thesis_accuracy": thesis_accuracy,
    }
