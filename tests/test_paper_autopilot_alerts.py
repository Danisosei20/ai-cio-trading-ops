from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from robinhood_tools.paper_autopilot import build_evidence, run_paper_entry, send_entry_alert


class FakeClock:
    def __init__(self, start: datetime):
        self.current = start
        self.sleeps = []

    def now(self):
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current = self.current + __import__("datetime").timedelta(seconds=seconds)


class Notifier:
    def __init__(self, failures=0):
        self.failures = failures
        self.messages = []

    def send_approval(self, *, channel_id, message):
        self.messages.append((channel_id, json.loads(message)))
        if len(self.messages) <= self.failures:
            raise RuntimeError("slack unavailable")
        return {"message_ts": "1700000000.1", "message_link": "https://slack.example/p/1"}


def accepted_result(tmp_path):
    now = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)
    evidence = build_evidence({
        "candidate_id": "candidate-alert",
        "symbol": "AAPL",
        "occ_symbol": "AAPL261218C00200000",
        "side": "call",
        "quantity": 1,
        "quote": {"reviewed_price": "2.50"},
        "thesis": "trend and demand support align",
        "policy_version": "paper-options-v1",
        "database_path": str(tmp_path / "autopilot.db"),
        "paper_only": True,
    }, now=now)
    result = run_paper_entry(
        evidence, execute=True, db_path=tmp_path / "autopilot.db", now=now,
        broker_call=lambda: {"status": "accepted", "order_id": "order-1", "price": "2.50", "side": "buy"},
        state_checker=lambda: {"validated": True, "held": False, "open_order": False},
        evidence_root=tmp_path,
    )
    result["evidence"] = evidence
    result["channel_id"] = "C123"
    return result


def test_accepted_order_alert_has_complete_payload_and_sends_immediately(tmp_path):
    result = accepted_result(tmp_path)
    notifier = Notifier()
    clock = FakeClock(datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc))

    delivery = send_entry_alert(result, notifier=notifier, clock=clock)

    assert delivery["sent"] is True
    assert delivery["attempts"] == 1
    assert clock.sleeps == []
    payload = notifier.messages[0][1]
    assert payload["timestamp"]
    assert payload["contract"] == "AAPL261218C00200000"
    assert payload["side"] == "buy"
    assert payload["price"] == "2.50"
    assert payload["thesis"] == "trend and demand support align"
    assert payload["policy_version"] == "paper-options-v1"
    assert payload["evidence_reference"].endswith(f"{result['candidate_id']}.json")

    with sqlite3.connect(tmp_path / "autopilot.db") as db:
        row = db.execute("SELECT status, attempts FROM paper_autopilot_alert_deliveries").fetchone()
    assert row == ("sent", 1)
    artifact = next((tmp_path / "evidence").glob("*.json"))
    assert json.loads(artifact.read_text())["slack"]["status"] == "sent"


def test_alert_retries_with_bounded_backoff_and_persists_failure_then_success(tmp_path):
    result = accepted_result(tmp_path)
    notifier = Notifier(failures=2)
    clock = FakeClock(datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc))

    delivery = send_entry_alert(result, notifier=notifier, clock=clock)

    assert delivery["sent"] is True
    assert delivery["attempts"] == 3
    assert clock.sleeps == [1, 2]
    with sqlite3.connect(tmp_path / "autopilot.db") as db:
        row = db.execute("SELECT status, attempts, last_error FROM paper_autopilot_alert_deliveries").fetchone()
    assert row == ("sent", 3, None)


def test_alert_failure_never_reinvokes_order_and_is_persisted(tmp_path):
    result = accepted_result(tmp_path)
    notifier = Notifier(failures=99)
    clock = FakeClock(datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc))

    delivery = send_entry_alert(result, notifier=notifier, clock=clock, retry_until_seconds=3)

    assert delivery["sent"] is False
    assert delivery["attempts"] == 3
    assert len(notifier.messages) == 3
    assert result["order"]["order_id"] == "order-1"
    with sqlite3.connect(tmp_path / "autopilot.db") as db:
        row = db.execute("SELECT status, attempts FROM paper_autopilot_alert_deliveries").fetchone()
    assert row == ("failed", 3)


def test_blocked_result_is_local_and_does_not_notify(tmp_path):
    notifier = Notifier()
    delivery = send_entry_alert({"action": "blocked", "reason": "closed"}, notifier=notifier,
                                clock=FakeClock(datetime.now(timezone.utc)))
    assert delivery["sent"] is False
    assert delivery["attempts"] == 0
    assert notifier.messages == []
