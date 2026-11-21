from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from robinhood_tools.market_calendar import MarketSession, entry_session_status
from robinhood_tools.models import OptionLeg, OptionOrderRequest
from robinhood_tools.options import OptionContract, OptionQuote
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.service import RobinhoodTradingService
from robinhood_tools.sim_broker import SimulationBroker
from robinhood_tools.universe import Sp500Snapshot
from robinhood_tools.runtime import build_paper_options_service
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


def test_watchlist_returns_quietly_before_loading_or_sending_on_holiday(capsys, monkeypatch):
    holiday = date(2026, 11, 26)
    import scripts.notify as notify

    monkeypatch.setattr(notify, "send", lambda _message: (_ for _ in ()).throw(
        AssertionError("holiday watchlist must not invoke Slack sender")
    ))
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


def test_holiday_entry_guard_blocks_buy_but_allows_sell_to_close():
    class MemoryApprovalStore:
        def reserve_option_execution(self, approval_id, request, review_id):
            return None

        def mark_executed(self, approval_id, order_id=None):
            return None

    contract = OptionContract("NVDA", "2026-12-18", Decimal("240"), "call", "NVDA261218C00240000")
    quote = OptionQuote(
        contract, Decimal("3.80"), Decimal("3.95"), Decimal("237.08"),
        datetime.now(timezone.utc).isoformat(), delta=Decimal("0.46"),
        open_interest=2000, volume=500,
    )
    broker = SimulationBroker(option_quotes={contract.option_symbol: quote})
    store = MemoryApprovalStore()
    snapshot = Sp500Snapshot(frozenset({"NVDA"}), datetime.now(timezone.utc).isoformat(), "https://example.test/sp500")

    def holiday_guard():
        status = entry_session_status(
            at(11, 0, day=date(2026, 11, 26)),
            clock={"is_open": False}, calendar=FakeCalendar(holiday_days={date(2026, 11, 26)}),
        )
        if not status["can_enter"]:
            raise PolicyViolation(status["reason"])

    open_service = RobinhoodTradingService(
        broker, approval_store=store, sp500_snapshot=snapshot,
        broker_environment="paper", require_human_confirmation=False,
    )
    guarded_service = RobinhoodTradingService(
        broker, approval_store=store, sp500_snapshot=snapshot,
        broker_environment="paper", require_human_confirmation=False,
        execution_guard=holiday_guard,
    )
    buy = OptionOrderRequest(
        account_id="sim-1", legs=(OptionLeg(
            symbol=contract.option_symbol, side="buy", effect="open", option_type="call",
            expiration_date=contract.expiry, strike_price=contract.strike, quantity=1,
        ),), order_type="limit", time_in_force="gfd", limit_price=quote.ask,
    )
    buy_review = open_service.review_option_order(buy)
    with pytest.raises(PolicyViolation):
        guarded_service.place_option_order(
            buy, review_id=buy_review.review_id, approval_id="blocked-buy", confirmed=False,
        )
    opened = open_service.place_option_order(
        buy, review_id=buy_review.review_id, approval_id="open-buy", confirmed=False,
    )
    assert opened.status == "filled"

    sell = OptionOrderRequest(
        account_id="sim-1", legs=(OptionLeg(
            symbol=contract.option_symbol, side="sell", effect="close", option_type="call",
            expiration_date=contract.expiry, strike_price=contract.strike, quantity=1,
        ),), order_type="limit", time_in_force="gfd", limit_price=quote.bid,
    )
    sell_review = guarded_service.review_option_order(sell)
    closed = guarded_service.place_option_order(
        sell, review_id=sell_review.review_id, approval_id="holiday-exit", confirmed=False,
    )
    assert closed.status == "filled"


def test_paper_options_service_wires_calendar_guard_into_entry_execution(tmp_path):
    class Settings:
        mode = "paper_auto"
        paper_broker = "alpaca"
        database_path = Path(tmp_path) / "options.db"
        paper_autonomy = type("Autonomy", (), {
            "earliest_entry_time_et": "10:15",
            "latest_entry_time_et": "15:45",
            "human_approval_required": False,
        })()

        def require_paper_trading(self):
            return None

    holiday = date(2026, 11, 26)
    service = build_paper_options_service(
        settings=Settings(), sp500_snapshot=None, transport=object(),
        session_clock={"is_open": False},
        exchange_calendar=FakeCalendar(holiday_days={holiday}),
        session_now=at(11, 0, day=holiday),
    )

    with pytest.raises(PolicyViolation, match="holiday|calendar"):
        service.execution_guard()
