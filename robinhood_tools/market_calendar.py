from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from .errors import PolicyViolation


class ExchangeCalendarProvider(Protocol):
    def session(self, day: date) -> "MarketSession": ...


class AlpacaExchangeCalendar:
    """Exchange-calendar provider backed by the paper Alpaca API."""

    def __init__(self, transport):
        self.transport = transport

    def session(self, day: date) -> "MarketSession":
        rows = self.transport.request("GET", f"/v2/calendar?start={day.isoformat()}&end={day.isoformat()}")
        if not isinstance(rows, list):
            raise PolicyViolation("Alpaca exchange calendar returned no unique session.")
        if not rows:
            return MarketSession(
                day, False, None, "https://paper-api.alpaca.markets/v2/calendar",
                datetime.now(ZoneInfo("UTC")).isoformat(), "exchange holiday",
            )
        if len(rows) != 1:
            raise PolicyViolation("Alpaca exchange calendar returned no unique session.")
        row = rows[0]
        if row.get("date") != day.isoformat() or not row.get("open") or not row.get("close"):
            return MarketSession(
                day, False, None, "https://paper-api.alpaca.markets/v2/calendar",
                datetime.now(ZoneInfo("UTC")).isoformat(), "exchange holiday",
            )
        return MarketSession(
            day, True, str(row["close"]), "https://paper-api.alpaca.markets/v2/calendar",
            datetime.now(ZoneInfo("UTC")).isoformat(),
        )


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
    *,
    earliest_entry_et: str = "10:15",
    latest_entry_et: str = "15:45",
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

    if calendar is None:
        result["reason"] = "authoritative exchange calendar is unavailable"
        return result

    try:
        session = calendar.session(now_et.date())
        session.validate()
    except Exception as exc:
        result["reason"] = f"exchange calendar could not be validated: {type(exc).__name__}"
        return result
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

    try:
        earliest = datetime.strptime(earliest_entry_et, "%H:%M").time()
        latest = datetime.strptime(latest_entry_et, "%H:%M").time()
    except (TypeError, ValueError):
        result["reason"] = "entry window configuration is invalid"
        return result
    in_entry_window = earliest <= now_et.time().replace(second=0, microsecond=0) <= latest
    result.update(
        status="OPEN",
        can_enter=in_entry_window,
        can_periodic_alert=True,
        entry_window={"earliest_et": earliest_entry_et, "latest_et": latest_entry_et},
        reason=(
            "exchange calendar and broker market clock are open"
            if in_entry_window else "outside configured entry window"
        ),
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
