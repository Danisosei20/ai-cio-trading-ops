"""Options data types and selection filters (Phase 2 PR 1).

Pure domain logic: no broker, no LLM, no I/O. The Options Scout proposes
from these types; the deterministic risk engine enforces the filters.
Long-only to start: maximum loss on any position is the premium paid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .errors import PolicyViolation


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    expiry: str  # YYYY-MM-DD
    strike: Decimal
    right: str  # "call" | "put"
    option_symbol: str  # OSI, e.g. NVDA261016C00240000

    def __post_init__(self) -> None:
        if self.right not in {"call", "put"}:
            raise PolicyViolation("Option right must be call or put.")
        try:
            date.fromisoformat(self.expiry)
        except ValueError as exc:
            raise PolicyViolation("Option expiry must be YYYY-MM-DD.") from exc
        if self.strike <= 0:
            raise PolicyViolation("Option strike must be positive.")
        if not self.option_symbol.strip():
            raise PolicyViolation("Option symbol is required.")
        object.__setattr__(self, "underlying", self.underlying.upper())

    def dte(self, today: str) -> int:
        return (date.fromisoformat(self.expiry) - date.fromisoformat(today)).days


@dataclass(frozen=True)
class OptionQuote:
    contract: OptionContract
    bid: Decimal
    ask: Decimal
    underlying_price: Decimal
    quoted_at: str  # ISO-8601 with timezone
    iv: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    open_interest: int = 0
    volume: int = 0

    def __post_init__(self) -> None:
        if self.bid < 0 or self.ask <= 0 or self.ask < self.bid:
            raise PolicyViolation("Option quote must have 0 <= bid <= ask, ask > 0.")
        if self.underlying_price <= 0:
            raise PolicyViolation("Underlying price must be positive.")

    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    def spread_pct(self) -> Decimal:
        mid = self.mid()
        return (self.ask - self.bid) / mid if mid > 0 else Decimal("1")


@dataclass(frozen=True)
class OptionSelectionFilters:
    min_dte: int = 7
    max_dte: int = 60
    max_spread_pct: Decimal = Decimal("0.10")
    min_option_volume: int = 100
    min_open_interest: int = 500
    min_delta: Decimal = Decimal("0.30")
    max_delta: Decimal = Decimal("0.60")
    allow_zero_dte: bool = False
    allow_short: bool = False  # long-only; spreads/naked reserved for later


@dataclass
class OptionRejection:
    option_symbol: str
    reasons: list[str] = field(default_factory=list)


def check_chain_quality(quotes: list[OptionQuote], *, max_age_minutes: int = 5,
                        now_iso: str | None = None) -> None:
    """Fail closed on incomplete or stale chains."""
    from datetime import datetime, timezone

    if not quotes:
        raise PolicyViolation("Option chain is empty.")
    now = datetime.fromisoformat(now_iso) if now_iso else datetime.now(timezone.utc)
    for q in quotes:
        ts = datetime.fromisoformat(q.quoted_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            raise PolicyViolation(f"Quote {q.contract.option_symbol} timestamp lacks timezone.")
        age = (now.astimezone(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 60
        if age < 0 or age > max_age_minutes:
            raise PolicyViolation(f"Quote {q.contract.option_symbol} is stale ({age:.1f}m).")


def select_long_option(quotes: list[OptionQuote], *, direction: str,
                       today: str, filters: OptionSelectionFilters | None = None
                       ) -> tuple[OptionQuote, list[OptionRejection]]:
    """Rank liquid long calls (bullish) or puts (bearish); list rejections with reasons."""
    if direction not in {"bullish", "bearish"}:
        raise PolicyViolation("Direction must be bullish or bearish.")
    filters = filters or OptionSelectionFilters()
    want = "call" if direction == "bullish" else "put"
    ranked: list[tuple[Decimal, OptionQuote]] = []
    rejected: list[OptionRejection] = []
    for q in quotes:
        reasons: list[str] = []
        c = q.contract
        if c.right != want:
            reasons.append(f"wrong right ({c.right}, want {want})")
        dte = c.dte(today)
        if not filters.allow_zero_dte and dte < 1:
            reasons.append("expired/too close (0DTE disabled)")
        elif not filters.min_dte <= dte <= filters.max_dte:
            reasons.append(f"DTE {dte} outside {filters.min_dte}-{filters.max_dte}")
        if q.spread_pct() > filters.max_spread_pct:
            reasons.append(f"spread {q.spread_pct():.1%} too wide")
        if q.volume < filters.min_option_volume:
            reasons.append(f"volume {q.volume} below minimum")
        if q.open_interest < filters.min_open_interest:
            reasons.append(f"OI {q.open_interest} below minimum")
        if q.delta is None or not filters.min_delta <= abs(q.delta) <= filters.max_delta:
            reasons.append(f"delta {q.delta} outside band")
        if reasons:
            rejected.append(OptionRejection(c.option_symbol, reasons))
            continue
        # Prefer at-the-money-ish liquid: score = spread penalty + distance from 0.45 delta.
        delta = q.delta
        assert delta is not None  # rejected above when None
        score = q.spread_pct() + abs(abs(delta) - Decimal("0.45"))
        ranked.append((score, q))
    if not ranked:
        raise PolicyViolation(f"No suitable long {want}: {len(rejected)} rejected.")
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1], rejected


def intrinsic_value(contract: OptionContract, underlying: Decimal) -> Decimal:
    if contract.right == "call":
        return max(underlying - contract.strike, Decimal("0"))
    return max(contract.strike - underlying, Decimal("0"))
