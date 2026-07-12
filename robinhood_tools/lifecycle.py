from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .errors import PolicyViolation

ExitAction = Literal["hold", "review_trim", "review_sell"]


@dataclass(frozen=True)
class PositionObservation:
    symbol: str
    quantity: Decimal
    average_cost: Decimal
    current_price: Decimal
    peak_price: Decimal
    holding_days: int
    thesis_status: Literal["strengthening", "unchanged", "weakening", "broken"]
    valuation_status: Literal["undervalued", "fair", "stretched", "extreme"]
    portfolio_weight: Decimal
    upcoming_binary_event: bool = False

    @property
    def return_pct(self) -> Decimal:
        return (self.current_price / self.average_cost) - Decimal("1")

    @property
    def drawdown_from_peak(self) -> Decimal:
        return (self.current_price / self.peak_price) - Decimal("1")


@dataclass(frozen=True)
class ExitDecision:
    action: ExitAction
    reason: str
    suggested_fraction: Decimal
    requires_broker_review: bool = True
    requires_codex_approval: bool = True


def evaluate_exit(
    observation: PositionObservation,
    *,
    target_return: Decimal,
    trailing_drawdown: Decimal = Decimal("0.08"),
    maximum_position_weight: Decimal = Decimal("0.10"),
) -> ExitDecision:
    """Decide whether to open a sell review; never authorizes or places an order."""
    if min(observation.quantity, observation.average_cost, observation.current_price, observation.peak_price) <= 0:
        raise PolicyViolation("Position quantity and prices must be positive.")
    if not Decimal("0") < trailing_drawdown < Decimal("1"):
        raise PolicyViolation("Trailing drawdown must be between zero and one.")

    if observation.thesis_status == "broken":
        return ExitDecision("review_sell", "Investment thesis is broken.", Decimal("1"))
    if observation.thesis_status == "weakening" and observation.return_pct > 0:
        return ExitDecision("review_trim", "Protect gains while the thesis is weakening.", Decimal("0.50"))
    if observation.portfolio_weight > maximum_position_weight:
        return ExitDecision("review_trim", "Position exceeds its maximum portfolio weight.", Decimal("0.25"))
    if observation.valuation_status == "extreme" and observation.return_pct > 0:
        return ExitDecision("review_trim", "Valuation is extreme and the position is profitable.", Decimal("0.50"))
    if observation.return_pct >= target_return and observation.drawdown_from_peak <= -trailing_drawdown:
        return ExitDecision(
            "review_trim",
            "Target was reached and gains have retraced from the peak; review locking in part of the gain.",
            Decimal("0.50"),
        )
    if observation.upcoming_binary_event and observation.return_pct >= target_return:
        return ExitDecision("review_trim", "Target is met ahead of a binary event.", Decimal("0.25"))
    return ExitDecision("hold", "No thesis, valuation, concentration, or target-based exit condition is met.", Decimal("0"))
