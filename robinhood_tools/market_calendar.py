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


def add_trading_days(provider: ExchangeCalendarProvider, start: date, count: int) -> date:
    if count < 0:
        raise PolicyViolation("Trading-day count cannot be negative.")
    current = start
    added = 0
    from datetime import timedelta
    while added < count:
        current += timedelta(days=1)
        session = provider.session(current)
        session.validate()
        if session.is_open:
            added += 1
    return current


def trading_days_until(provider: ExchangeCalendarProvider, start: date, end: date) -> int:
    if end < start:
        return -trading_days_until(provider, end, start)
    from datetime import timedelta
    current = start
    count = 0
    while current < end:
        current += timedelta(days=1)
        session = provider.session(current)
        session.validate()
        count += int(session.is_open)
    return count
