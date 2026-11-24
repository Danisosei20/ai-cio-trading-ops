#!/usr/bin/env python3
"""Time-aware freshness: live eligibility vs historical replay validity.

A historically valid signal must not be rejected because the wall clock is
later; a replay-valid signal must never become eligible for a current order.
Uses market context captured at signal time, not today's clock.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date as date_type, datetime, time, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Minimal, explicit US equity holiday calendar for 2026 (verified by owner
# configuration where available). Unknown dates fail closed as "unknown".
HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
}
EARLY_CLOSES_2026 = {"2026-11-27", "2026-12-24"}


@dataclass
class FreshnessResult:
    status: str
    reason: str
    session: str
    market_as_of: str
    age_seconds: float | None
    usable_for_live_entry: bool
    valid_for_replay: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Naive timestamps are assumed to be exchange-local (America/New_York),
    # never UTC. This prevents a post-close ET bar from being read as midday.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(timezone.utc)


def market_session(day: str) -> tuple[str, time]:
    """Return (session_type, close_time) for a date; unknown fails closed."""
    if not re_match_date(day):
        return "unknown", REGULAR_CLOSE
    try:
        parsed = date_type.fromisoformat(day)
    except ValueError:
        return "unknown", REGULAR_CLOSE
    if parsed.year != 2026:  # only the explicit 2026 calendar is verified
        return "unknown", REGULAR_CLOSE
    if parsed.weekday() >= 5:
        return "weekend", REGULAR_CLOSE
    if day in HOLIDAYS_2026:
        return "holiday", REGULAR_CLOSE
    if day in EARLY_CLOSES_2026:
        return "early_close", EARLY_CLOSE
    return "regular", REGULAR_CLOSE


def re_match_date(value: str) -> bool:
    import re  # noqa: PLC0415
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", str(value)))


def evaluate(*, mode: str, market_as_of: str, retrieved_at: str,
             now: str | None = None, max_age_seconds: float = 300,
             timezone_name: str = "America/New_York",
             feed: str = "iex", capability: str = "SUPPORTED",
             completed_bar: bool = False, bars_present: bool = False,
             interval_minutes: int = 5) -> FreshnessResult:
    """Evaluate a signal against the context in which it was captured."""
    as_of = _parse(market_as_of)
    retrieved = _parse(retrieved_at)
    current = _parse(now) if now else datetime.now(timezone.utc)
    if as_of is None or retrieved is None or current is None:
        return FreshnessResult("rejected", "missing or malformed timestamps",
                               "unknown", market_as_of, None, False, False)
    if str(feed).lower() not in {"iex", "sip", "delayed_sip"}:
        return FreshnessResult("unsupported", f"unsupported feed {feed!r}",
                               "unknown", market_as_of, None, False, False)
    try:
        ZoneInfo(timezone_name)
    except Exception:  # noqa: BLE001 - invalid timezone fails closed
        return FreshnessResult("rejected", f"invalid timezone {timezone_name!r}",
                               "unknown", market_as_of, None, False, False)
    day = as_of.astimezone(ET).date().isoformat()
    session, close = market_session(day)
    if session != "regular" and session != "early_close":
        return FreshnessResult("rejected", f"non-trading session: {session}",
                               session, market_as_of, None, False, False)
    if as_of > current:
        return FreshnessResult("rejected", "future-dated market evidence",
                               session, market_as_of, None, False, False)
    if retrieved < as_of:
        return FreshnessResult("rejected", "retrieval before market timestamp",
                               session, market_as_of, None, False, False)
    in_session = as_of.astimezone(ET).time()
    if not (REGULAR_OPEN <= in_session <= close):
        return FreshnessResult("rejected", "outside regular/early-close session",
                               session, market_as_of, None, False, False)
    if not bars_present or not completed_bar:
        return FreshnessResult("rejected", "missing or incomplete bars",
                               session, market_as_of, None, False, False)
    if capability != "SUPPORTED":
        return FreshnessResult("unsupported", f"data capability {capability}",
                               session, market_as_of, None, False, False)

    age = (current - as_of).total_seconds()
    if mode == "live":
        if age < 0 or age > max_age_seconds:
            return FreshnessResult("stale", "live evidence outside freshness window",
                                   session, market_as_of, age, False, False)
        return FreshnessResult("fresh", "live evidence within freshness window",
                               session, market_as_of, age, True, True)
    if mode == "replay":
        replay_age = max(0.0, (retrieved - as_of).total_seconds())
        if replay_age > max_age_seconds:
            return FreshnessResult("stale", "original snapshot was already stale",
                                   session, market_as_of, replay_age, False, False)
        return FreshnessResult("historically_valid", "valid at original signal time",
                               session, market_as_of, replay_age, False, True)
    if mode == "post_close":
        return FreshnessResult("post_close", "analysis only; not an order path",
                               session, market_as_of, age, False, True)
    return FreshnessResult("rejected", f"unknown mode {mode!r}",
                           session, market_as_of, None, False, False)
