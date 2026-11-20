from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from robinhood_tools.market_calendar import MarketSession, entry_session_status
from scripts.options_watchlist import main as watchlist_main
from scripts.options_watchlist import periodic_check_status


ET = ZoneInfo("America/New_York")


class FakeCalendar:
    def __init__(self, *, open_days=None, holiday_days=()):
        self.open_days = set(open_days or ())
        self.holiday_days = set(holiday_days)

    def session(self, day):
        is_open = day in self.open_days
        return MarketSession(
            day=day,
            is_open=is_open,
            close_time_et="16:00" if is_open else None,
            source_url="https://example.test/exchange-calendar",
            verified_at="2026-11-20T14:00:00+00:00",
            reason="exchange holiday" if day in self.holiday_days else "closed",
        )


def at(hour, minute, *, day=date(2026, 11, 20)):
    return datetime.combine(day, time(hour, minute), tzinfo=ET)


def test_regular_closed_hours_suppress_entry_and_periodic_alerts():
    day = date(2026, 11, 20)
    result = entry_session_status(
        at(8, 30, day=day),
        clock={"is_open": False},
        calendar=FakeCalendar(open_days={day}),
    )

    assert result["status"] == "CLOSED"
    assert result["can_enter"] is False
    assert result["can_periodic_alert"] is False


def test_weekends_are_closed_even_if_clock_claims_open():
    saturday = date(2026, 11, 21)
    result = entry_session_status(
        at(11, 0, day=saturday),
        clock={"is_open": True},
        calendar=FakeCalendar(open_days={saturday}),
    )

    assert result["status"] == "WEEKEND"
    assert result["can_enter"] is False
    assert result["can_periodic_alert"] is False


def test_exchange_holidays_suppress_periodic_alerts():
    holiday = date(2026, 11, 26)
    result = entry_session_status(
        at(11, 0, day=holiday),
        clock={"is_open": False},
        calendar=FakeCalendar(holiday_days={holiday}),
    )

    assert result["status"] == "HOLIDAY"
    assert result["can_enter"] is False
    assert result["can_periodic_alert"] is False


def test_holiday_periodic_watchlist_is_suppressed_before_scan_or_alert():
    holiday = date(2026, 11, 26)
    result = periodic_check_status(
        at(15, 10, day=holiday),
        clock={"is_open": False},
        calendar=FakeCalendar(holiday_days={holiday}),
    )

    assert result["status"] == "HOLIDAY"
    assert result["can_periodic_alert"] is False


def test_watchlist_returns_quietly_before_loading_or_sending_on_holiday(capsys):
    holiday = date(2026, 11, 26)
    assert watchlist_main(
        ["--send"],
        now=at(15, 10, day=holiday),
        clock={"is_open": False},
        calendar=FakeCalendar(holiday_days={holiday}),
    ) == 0
    assert "periodic_check_skipped" in capsys.readouterr().out


def test_open_session_allows_entry_and_periodic_alerts():
    day = date(2026, 11, 20)
    result = entry_session_status(
        at(11, 0, day=day),
        clock={"is_open": True},
        calendar=FakeCalendar(open_days={day}),
    )

    assert result["status"] == "OPEN"
    assert result["can_enter"] is True
    assert result["can_periodic_alert"] is True


def test_missing_calendar_fails_closed_even_when_clock_claims_open():
    result = entry_session_status(at(11, 0), clock={"is_open": True}, calendar=None)

    assert result["status"] == "CLOSED"
    assert result["can_enter"] is False
    assert result["can_periodic_alert"] is False


def test_unvalidated_calendar_fails_closed_even_when_clock_claims_open():
    class UnvalidatedCalendar:
        def session(self, day):
            return MarketSession(day, True, "16:00", "http://untrusted", "")

    result = entry_session_status(
        at(11, 0), clock={"is_open": True}, calendar=UnvalidatedCalendar()
    )

    assert result["status"] == "CLOSED"
    assert result["can_enter"] is False
    assert result["can_periodic_alert"] is False


def test_open_session_outside_configured_entry_window_cannot_authorize_entry():
    day = date(2026, 11, 20)
    result = entry_session_status(
        at(15, 50, day=day),
        clock={"is_open": True},
        calendar=FakeCalendar(open_days={day}),
        earliest_entry_et="10:15",
        latest_entry_et="15:45",
    )

    assert result["status"] == "OPEN"
    assert result["can_enter"] is False
    assert result["can_periodic_alert"] is True
    assert "entry window" in result["reason"]


def test_exit_observation_remains_available_outside_entry_sessions():
    for day, clock in (
        (date(2026, 11, 21), {"is_open": False}),
        (date(2026, 11, 26), {"is_open": False}),
        (date(2026, 11, 20), {"is_open": False}),
    ):
        result = entry_session_status(
            at(8, 30, day=day),
            clock=clock,
            calendar=FakeCalendar(holiday_days={date(2026, 11, 26)}),
        )
        assert result["can_observe_exits"] is True
