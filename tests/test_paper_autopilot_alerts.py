from __future__ import annotations

import json
import sqlite3
import threading
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
        "mode": "paper_auto",
        "provenance": "paper",
    }, now=now)
    result = run_paper_entry(
        evidence, execute=True, db_path=tmp_path / "autopilot.db", now=now,
        broker_call=lambda: {"status": "accepted", "order_id": "order-1", "price": "2.50", "side": "buy"},
        state_checker=lambda: {"validated": True, "held": False, "open_order": False},
        evidence_root=tmp_path,
    )
    result["evidence"] = evidence
    result["paper_only"] = True
    result["mode"] = "paper_auto"
    result["provenance"] = "paper"
    result["channel_id"] = "C123"
    return result


class BlockingNotifier(Notifier):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def send_approval(self, *, channel_id, message):
        self.started.set()
        self.release.wait(timeout=5)
        return super().send_approval(channel_id=channel_id, message=message)


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
        row = db.execute("SELECT status, attempts, order_id, order_status, evidence_reference "
                         "FROM paper_autopilot_alert_deliveries").fetchone()
    assert row[:4] == ("sent", 1, "order-1", "accepted")
    assert row[4].endswith(f"{result['candidate_id']}.json")
    artifact = next((tmp_path / "evidence").glob("*.json"))
    artifact_payload = json.loads(artifact.read_text())
    assert artifact_payload["slack"]["status"] == "sent"
    assert artifact_payload["order"] == {"order_id": "order-1", "price": "2.50",
                                           "side": "buy", "status": "accepted"}
    assert artifact_payload["order"]["order_id"] == result["order"]["order_id"]


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


def test_missing_slack_config_records_delivery_failure_without_raising(tmp_path):
    result = accepted_result(tmp_path)
    delivery = send_entry_alert(
        result, notifier=None,
        clock=FakeClock(datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)),
    )

    assert delivery["sent"] is False
    assert delivery["status"] == "failed"
    assert delivery["last_error"]["code"] == "notifier_config_missing"
    with sqlite3.connect(tmp_path / "autopilot.db") as db:
        assert db.execute("SELECT status, last_error FROM paper_autopilot_alert_deliveries").fetchone() == (
            "failed", "notifier_config_missing"
        )
    artifact = next((tmp_path / "evidence").glob("*.json"))
    assert json.loads(artifact.read_text())["slack"]["status"] == "failed"


def test_invalid_notifier_is_structured_and_does_not_raise(tmp_path):
    result = accepted_result(tmp_path)
    delivery = send_entry_alert(
        result, notifier=object(),
        clock=FakeClock(datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)),
        retry_until_seconds=0,
    )

    assert delivery["sent"] is False
    assert delivery["last_error"]["code"] == "notifier_invalid"


def test_retry_window_is_hard_capped_at_ten_minutes(tmp_path):
    result = accepted_result(tmp_path)
    clock = FakeClock(datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc))
    delivery = send_entry_alert(result, notifier=Notifier(failures=999), clock=clock,
                                retry_until_seconds=100_000)

    assert delivery["sent"] is False
    assert sum(clock.sleeps) <= 600
    assert clock.current.timestamp() <= datetime(2026, 11, 20, 15, 40,
                                                   tzinfo=timezone.utc).timestamp()


def test_already_sent_delivery_is_idempotent_before_notifier_validation(tmp_path):
    result = accepted_result(tmp_path)
    clock = FakeClock(datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc))
    first = send_entry_alert(result, notifier=Notifier(), clock=clock)

    second = send_entry_alert(result, notifier=None, clock=clock)

    assert first["sent"] is True
    assert second == {
        "sent": True,
        "attempts": 1,
        "last_error": None,
        "sent_at": first["sent_at"],
        "status": "sent",
        "message_ts": "1700000000.1",
        "message_link": "https://slack.example/p/1",
    }


def test_send_success_persistence_failure_is_unknown_and_not_retryable(tmp_path, monkeypatch):
    result = accepted_result(tmp_path)
    notifier = Notifier()
    original_persist = __import__("robinhood_tools.paper_autopilot", fromlist=["_persist_alert_delivery"])._persist_alert_delivery
    calls = []

    def fail_after_intent(*args, **kwargs):
        calls.append(kwargs["status"])
        if kwargs["status"] == "sent":
            raise OSError("database became unavailable")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr("robinhood_tools.paper_autopilot._persist_alert_delivery", fail_after_intent)
    delivery = send_entry_alert(result, notifier=notifier,
                                clock=FakeClock(datetime(2026, 11, 20, 15, 30,
                                                         tzinfo=timezone.utc)))

    assert delivery["status"] == "indeterminate"
    assert delivery["last_error"]["code"] == "persistence_error"
    assert len(notifier.messages) == 1
    with sqlite3.connect(tmp_path / "autopilot.db") as db:
        assert db.execute("SELECT status FROM paper_autopilot_alert_deliveries").fetchone() == ("unknown_delivery",)
    retry = send_entry_alert(result, notifier=Notifier(),
                             clock=FakeClock(datetime(2026, 11, 20, 15, 30,
                                                      tzinfo=timezone.utc)))
    assert retry["status"] == "indeterminate"
    assert retry["last_error"]["code"] == "delivery_state_indeterminate"


def test_delivery_state_read_failure_fails_closed_without_sending(tmp_path):
    result = accepted_result(tmp_path)
    db_path = tmp_path / "autopilot.db"
    db_path.write_bytes(b"not a sqlite database")
    notifier = Notifier()

    delivery = send_entry_alert(result, notifier=notifier,
                                clock=FakeClock(datetime(2026, 11, 20, 15, 30,
                                                         tzinfo=timezone.utc)))

    assert delivery["status"] == "indeterminate"
    assert delivery["last_error"]["code"] == "persistence_read_failed"
    assert notifier.messages == []


def test_concurrent_alerts_atomically_claim_one_delivery_owner(tmp_path):
    result = accepted_result(tmp_path)
    notifier = BlockingNotifier()
    clock = FakeClock(datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc))
    results = []

    first = threading.Thread(target=lambda: results.append(
        send_entry_alert(result, notifier=notifier, clock=clock)))
    first.start()
    assert notifier.started.wait(timeout=5)
    second = send_entry_alert(result, notifier=Notifier(), clock=clock)
    notifier.release.set()
    first.join(timeout=5)

    assert len(results) == 1
    assert results[0]["sent"] is True
    assert second["status"] == "indeterminate"
    assert second["last_error"]["code"] == "delivery_state_indeterminate"
    assert len(notifier.messages) == 1


def test_retrying_state_persistence_failure_stops_before_another_send(tmp_path, monkeypatch):
    result = accepted_result(tmp_path)
    notifier = Notifier(failures=1)
    original = __import__("robinhood_tools.paper_autopilot", fromlist=["_persist_alert_delivery"])._persist_alert_delivery

    def fail_retry(*args, **kwargs):
        if kwargs["status"] == "retrying":
            raise OSError("retry state unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr("robinhood_tools.paper_autopilot._persist_alert_delivery", fail_retry)
    delivery = send_entry_alert(result, notifier=notifier,
                                clock=FakeClock(datetime(2026, 11, 20, 15, 30,
                                                         tzinfo=timezone.utc)))

    assert delivery["status"] == "indeterminate"
    assert delivery["last_error"]["code"] == "retry_persistence_failed"
    assert len(notifier.messages) == 1


def test_live_misrouted_or_incoherent_accepted_result_is_refused(tmp_path):
    cases = [
        {"mode": "live_approval"},
        {"provenance": "live"},
        {"paper_only": False},
        {"order": {"status": "rejected", "order_id": "order-1"}},
    ]
    for index, changes in enumerate(cases):
        result = accepted_result(tmp_path / str(index))
        result.update(changes)
        notifier = Notifier()

        delivery = send_entry_alert(result, notifier=notifier,
                                    clock=FakeClock(datetime(2026, 11, 20, 15, 30,
                                                             tzinfo=timezone.utc)))

        assert delivery["status"] == "blocked"
        assert delivery["last_error"] is not None, (changes, delivery, result.get("action"), result.get("reason"))
        assert delivery["last_error"]["code"] == "paper_alert_refused"
        assert notifier.messages == []
