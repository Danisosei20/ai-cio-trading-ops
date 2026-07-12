from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from .errors import PolicyViolation


class ExchangeCalendarProvider(Protocol):
    def session(self, day: date) -> "MarketSession": ...


@dataclass(frozen=True)
class MarketSession:
    day: date
    is_open: bool
    close_time_et: str | None
    source_url: str
    verified_at: str
    reason: str = ""

    def validate(self):
        if not self.source_url.startswith("https://") or not self.verified_at:
            raise PolicyViolation("Market-calendar result requires a current authoritative source and timestamp.")


def require_open_session(provider: ExchangeCalendarProvider, day: date) -> MarketSession:
    session = provider.session(day)
    session.validate()
    if not session.is_open:
        raise PolicyViolation(f"U.S. equity market is closed: {session.reason or day.isoformat()}.")
    return session
