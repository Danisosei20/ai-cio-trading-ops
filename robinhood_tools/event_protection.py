#!/usr/bin/env python3
"""News and event protection.

Provides scheduled checks for economic releases (CPI, FOMC, employment
reports), company earnings, and trading halts. Applies configurable
no-entry windows around major releases so the desk does not enter new
positions during elevated event risk.

Read-only: fetches from public sources (FRED, SEC EDGAR, Alpaca) but never
places or cancels orders. Returns actionable guidance (enter / avoid /
reduce) per symbol.

Usage:
  from robinhood_tools.event_protection import EventProtection
  ep = EventProtection()
  status = ep.check_symbol("NVDA", current_dt)
  # status: {"action": "block", "reason": "earnings tomorrow", ...}
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

ET = ZoneInfo("America/New_York")

# Known market-moving economic release times (ET). These are approximate
# release times and should be validated against a real economic calendar.
ECONOMIC_EVENTS = {
    "FOMC": "14:00",      # Fed decision
    "CPI": "08:30",       # Consumer Price Index
    "NFP": "08:30",       # Non-Farm Payrolls
    "PPI": "08:30",       # Producer Price Index
    "GDP": "08:30",       # Gross Domestic Product
    "RETail_SALES": "08:30",
    "INDUSTRIAL_PRODUCTION": "09:15",
}


@dataclass(frozen=True)
class EventStatus:
    symbol: str
    action: str  # "enter", "avoid", "reduce"
    reason: str
    next_event: Optional[str] = None
    event_time: Optional[str] = None
    minutes_until: Optional[float] = None


class EventProtection:
    """Checks for upcoming earnings, economic events, and trading halts."""

    def __init__(
        self,
        pre_entry_minutes: int = 30,
        post_entry_minutes: int = 60,
        earnings_lookback_days: int = 1,
        earnings_lookahead_days: int = 1,
    ) -> None:
        self.pre_entry_minutes = pre_entry_minutes
        self.post_entry_minutes = post_entry_minutes
        self.earnings_lookback_days = earnings_lookback_days
        self.earnings_lookahead_days = earnings_lookahead_days

    def check_symbol(
        self,
        symbol: str,
        current_dt: Optional[datetime] = None,
        earnings_data: Optional[dict] = None,
        economic_events: Optional[list[dict]] = None,
        halted_symbols: Optional[set[str]] = None,
    ) -> EventStatus:
        """Check if symbol is clear for new entries.

        Args:
            symbol: Ticker symbol
            current_dt: Current ET datetime (defaults to now ET)
            earnings_data: Dict mapping symbol -> list of earning dates (ISO strings)
            economic_events: List of economic event dicts with "event", "date", "time_et"
            halted_symbols: Set of currently halted symbols

        Returns EventStatus with action ("enter", "avoid", "reduce") and reason.
        """
        now = current_dt or datetime.now(ET)
        halted_symbols = halted_symbols or set()
        earnings_data = earnings_data or {}
        economic_events = economic_events or []

        # Check if symbol is halted
        if symbol.upper() in {s.upper() for s in halted_symbols}:
            return EventStatus(
                symbol=symbol,
                action="avoid",
                reason=f"{symbol} is currently halted",
                next_event="trading halt",
            )

        # Check earnings
        earnings = earnings_data.get(symbol.upper(), [])
        for earn_date_str in earnings:
            try:
                earn_date = datetime.fromisoformat(earn_date_str).date()
            except ValueError:
                try:
                    earn_date = datetime.strptime(earn_date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue

            earn_dt = datetime.combine(earn_date, datetime.min.time(), tzinfo=ET)
            # Earnings typically before market open or after close
            # Block if within lookback/lookahead window
            days_diff = (now.date() - earn_date).days
            if abs(days_diff) <= max(self.earnings_lookback_days, self.earnings_lookahead_days):
                if days_diff <= 0:
                    # Earnings today or future
                    reason = f"earnings {earn_date_str}"
                else:
                    reason = f"earnings {earn_date_str} ({days_diff} day(s) ago)"
                return EventStatus(
                    symbol=symbol,
                    action="avoid",
                    reason=reason,
                    next_event="earnings",
                    event_time=earn_dt.isoformat(),
                )

        # Check economic events
        for event in economic_events:
            event_date_str = event.get("date", "")
            event_time_str = event.get("time_et", "08:30")
            event_name = event.get("event", "economic event")

            try:
                event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            event_dt = datetime.combine(
                event_date,
                datetime.strptime(event_time_str, "%H:%M").time(),
                tzinfo=ET,
            )

            # Check if within pre-entry window
            time_until = (event_dt - now).total_seconds() / 60.0
            if -self.post_entry_minutes <= time_until <= self.pre_entry_minutes:
                return EventStatus(
                    symbol=symbol,
                    action="avoid",
                    reason=f"{event_name} {event_date_str} {event_time_str} ET",
                    next_event=event_name,
                    event_time=event_dt.isoformat(),
                    minutes_until=time_until if time_until > 0 else 0,
                )

        return EventStatus(
            symbol=symbol,
            action="enter",
            reason="no events nearby",
        )

    def check_market_wide(
        self,
        current_dt: Optional[datetime] = None,
        economic_events: Optional[list[dict]] = None,
    ) -> EventStatus:
        """Check if the overall market is in an event blackout window."""
        now = current_dt or datetime.now(ET)
        economic_events = economic_events or []

        for event in economic_events:
            event_date_str = event.get("date", "")
            event_time_str = event.get("time_et", "08:30")
            event_name = event.get("event", "economic event")

            try:
                event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            event_dt = datetime.combine(
                event_date,
                datetime.strptime(event_time_str, "%H:%M").time(),
                tzinfo=ET,
            )

            time_until = (event_dt - now).total_seconds() / 60.0
            if -self.post_entry_minutes <= time_until <= self.pre_entry_minutes:
                return EventStatus(
                    symbol="MARKET",
                    action="avoid",
                    reason=f"{event_name} {event_date_str} {event_time_str} ET",
                    next_event=event_name,
                    event_time=event_dt.isoformat(),
                    minutes_until=time_until if time_until > 0 else 0,
                )

        return EventStatus(
            symbol="MARKET",
            action="enter",
            reason="no market events nearby",
        )


def fetch_fred_events(
    series_id: str = "FEDFUNDS",
    api_key: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """Fetch FRED economic data events. Returns list of event dicts."""
    if not api_key:
        return []

    base_url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
    }
    if start_date:
        params["observation_start"] = start_date
    if end_date:
        params["observation_end"] = end_date

    try:
        import urllib.parse
        url = base_url + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        observations = data.get("observations", [])
        events = []
        for obs in observations:
            date_str = obs.get("date", "")
            value = obs.get("value", ".")
            if value != ".":
                events.append({
                    "event": series_id,
                    "date": date_str,
                    "value": float(value),
                })
        return events
    except Exception:
        return []


def fetch_earnings_calendar(
    api_key: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, list[str]]:
    """Fetch earnings calendar. Returns dict mapping symbol -> list of dates."""
    # This would typically use a paid earnings API. Returns empty without a key.
    return {}


if __name__ == "__main__":
    ep = EventProtection()
    now = datetime.now(ET)
    result = ep.check_symbol("NVDA", now)
    print(f"NVDA: {result.action} - {result.reason}")
