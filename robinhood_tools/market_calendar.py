from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

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


def entry_session_status(
    now: datetime,
    clock: dict | None = None,
    calendar: ExchangeCalendarProvider | None = None,
) -> dict[str, object]:
    """Return the fail-closed entry/periodic policy for an exchange session.

    The Alpaca clock confirms the live session; the exchange calendar confirms
    that the date is a trading day.  Exit observation is deliberately exposed
    separately so callers can skip entry work without disabling protection.
    ``now`` is interpreted in Eastern time when naive, since the exchange
    calendar is keyed by the exchange date.
    """
    if now.tzinfo is None:
        now_et = now.replace(tzinfo=ZoneInfo("America/New_York"))
    else:
        now_et = now.astimezone(ZoneInfo("America/New_York"))

    result: dict[str, object] = {
        "status": "CLOSED",
        "can_enter": False,
        "can_periodic_alert": False,
        "can_observe_exits": True,
        "session_date": now_et.date().isoformat(),
        "reason": "market session is not confirmed open",
    }

    if now_et.weekday() >= 5:
        result.update(status="WEEKEND", reason="exchange weekend")
        return result

    session = None
    if calendar is not None:
        session = calendar.session(now_et.date())
        session.validate()
        if not session.is_open:
            result.update(
                # Weekends were handled above; a closed exchange-calendar
                # session on a weekday is therefore a holiday/non-trading day.
                status="HOLIDAY",
                reason=session.reason or "exchange calendar is closed",
            )
            return result

    if not bool((clock or {}).get("is_open")):
        result["reason"] = "broker market clock is closed"
        return result

    result.update(
        status="OPEN",
        can_enter=True,
        can_periodic_alert=True,
        reason="exchange calendar and broker market clock are open",
    )
    return result


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
