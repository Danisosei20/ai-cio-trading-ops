from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from .errors import PolicyViolation

MarketRegime = Literal["risk_on", "neutral", "risk_off"]


@dataclass(frozen=True)
class DataQuality:
    quote_fresh: bool
    membership_fresh: bool
    news_fresh: bool
    fundamentals_present: bool
    volume_present: bool
    event_calendar_present: bool

    @property
    def score(self) -> int:
        return round(100 * sum((self.quote_fresh, self.membership_fresh, self.news_fresh,
                                self.fundamentals_present, self.volume_present,
                                self.event_calendar_present)) / 6)

    def require_complete(self) -> None:
        if self.score < 100:
            raise PolicyViolation(f"Data quality is incomplete ({self.score}/100); recommendation blocked.")


def classify_market_regime(*, spy_above_200d: bool, volatility_pct: Decimal,
                           breadth_pct: Decimal, credit_stress: bool) -> MarketRegime:
    if credit_stress or volatility_pct >= Decimal("0.30") or breadth_pct < Decimal("0.35"):
        return "risk_off"
    if spy_above_200d and volatility_pct < Decimal("0.20") and breadth_pct >= Decimal("0.55"):
        return "risk_on"
    return "neutral"


def require_regime_hurdle(regime: MarketRegime, *, score: int) -> None:
    minimum = {"risk_on": 90, "neutral": 93, "risk_off": 97}[regime]
    if score < minimum:
        raise PolicyViolation(f"{regime} regime requires candidate score >= {minimum}.")


def require_earnings_clear(*, today: date, earnings_date: date | None, blackout_days: int = 5) -> None:
    if earnings_date is None:
        raise PolicyViolation("Next earnings date is unavailable.")
    days = (earnings_date - today).days
    if 0 <= days <= blackout_days:
        raise PolicyViolation(f"New purchase blocked within {blackout_days} days of earnings.")
