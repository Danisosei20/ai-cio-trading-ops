from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from .errors import PolicyViolation

Signal = Literal["supportive", "neutral", "conflicting", "unavailable"]
SourceQuality = Literal["primary", "independent", "weak"]


@dataclass(frozen=True)
class ResearchSource:
    url: str
    title: str
    quality: SourceQuality
    observed_at: str


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    observed_at: str
    quote_source: str
    price: Decimal
    bid: Decimal
    ask: Decimal
    session_volume: int
    avg_volume_20d: int
    avg_volume_50d: int
    avg_daily_dollar_volume: Decimal
    relative_volume: Decimal
    return_20d: Decimal
    return_60d: Decimal
    relative_strength_sp500: Decimal
    realized_volatility_20d: Decimal
    atr_14d: Decimal
    max_drawdown: Decimal
    next_earnings_date: str | None
    signal: Signal
    sources: tuple[ResearchSource, ...]

    @property
    def spread_pct(self) -> Decimal:
        midpoint = (self.bid + self.ask) / 2
        return Decimal("0") if midpoint == 0 else (self.ask - self.bid) / midpoint

    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def validate(self, *, max_age_minutes: int = 15, min_sources: int = 2) -> None:
        observed = datetime.fromisoformat(self.observed_at)
        if observed.tzinfo is None:
            raise PolicyViolation("Market snapshot timestamp must include a timezone.")
        age = datetime.now(timezone.utc) - observed.astimezone(timezone.utc)
        if age.total_seconds() < 0 or age.total_seconds() > max_age_minutes * 60:
            raise PolicyViolation("Market snapshot is stale; refresh quote, volume, and spread data.")
        positive = (self.price, self.bid, self.ask, self.avg_daily_dollar_volume, self.atr_14d)
        if any(value <= 0 for value in positive) or self.ask < self.bid:
            raise PolicyViolation("Market snapshot contains invalid price or liquidity values.")
        if min(self.session_volume, self.avg_volume_20d, self.avg_volume_50d) <= 0:
            raise PolicyViolation("Current, 20-day, and 50-day volume are required.")
        if self.signal == "unavailable":
            raise PolicyViolation("Critical market-signal analysis is unavailable.")
        primary = [source for source in self.sources if source.quality == "primary"]
        independent = [source for source in self.sources if source.quality == "independent"]
        if len(self.sources) < min_sources or not primary or not independent:
            raise PolicyViolation("Research requires primary evidence and an independent reliable source.")
        for source in self.sources:
            if not source.url.startswith("https://"):
                raise PolicyViolation("Research sources must use HTTPS URLs.")


@dataclass(frozen=True)
class TradeCandidate:
    snapshot: MarketSnapshot
    thesis: str
    counter_argument: str
    score: int
    reward_risk: Decimal
    intended_price: Decimal
    invalidation_level: Decimal
    target_or_review_condition: str
    expected_portfolio_weight: Decimal
    max_slippage_pct: Decimal

    def validate(self, *, max_spread_pct: Decimal, max_position_weight: Decimal) -> None:
        self.snapshot.validate()
        if self.score < 90:
            raise PolicyViolation("New purchases require an exceptional score of at least 90.")
        if self.reward_risk < Decimal("2"):
            raise PolicyViolation("Expected reward/risk must be at least 2:1.")
        if not self.thesis.strip() or not self.counter_argument.strip():
            raise PolicyViolation("A thesis and counter-argument are required.")
        if self.snapshot.spread_pct > max_spread_pct:
            raise PolicyViolation("Bid/ask spread exceeds the configured execution limit.")
        if self.expected_portfolio_weight > max_position_weight:
            raise PolicyViolation("Expected position weight exceeds the configured limit.")
        if self.intended_price <= 0 or self.invalidation_level <= 0:
            raise PolicyViolation("Intended price and invalidation level must be positive.")


@dataclass(frozen=True)
class ExitPlan:
    symbol: str
    thesis: str
    created_at: str
    fair_value_low: Decimal
    fair_value_high: Decimal
    invalidation_condition: str
    target_or_review_condition: str
    maximum_position_weight: Decimal
    expected_holding_period: str
    earnings_event_plan: str
    tax_notes: str
