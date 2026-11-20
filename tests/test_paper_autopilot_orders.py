from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone

from robinhood_tools.paper_autopilot import build_evidence, run_paper_entry
from scripts.options_pilot import validate_paper_option_state


def evidence(tmp_path, **extra):
    raw = {
        "candidate_id": "candidate-1",
        "symbol": "AAPL",
        "occ_symbol": "AAPL261218C00200000",
        "side": "call",
        "quantity": 1,
        "database_path": str(tmp_path / "autopilot.db"),
        "paper_only": True,
    }
    raw.update(extra)
    built = build_evidence(raw, now=datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc))
    built.update({key: value for key, value in raw.items() if key not in built})
    built.setdefault("state_checker", lambda: {"validated": True, "held": False, "open_order": False})
    return built


def test_exact_occ_duplicate_is_rejected_without_execution(tmp_path):
    first = evidence(tmp_path)
    second = evidence(tmp_path, candidate_id="candidate-2", premium=99)
    calls = []

    assert run_paper_entry(first, execute=True, owner="worker-a", broker_call=lambda: calls.append(1) or {"status": "accepted", "order_id": "1"})["action"] == "accepted"
    result = run_paper_entry(second, execute=True, owner="worker-a", broker_call=lambda: calls.append(2) or {"status": "accepted", "order_id": "2"})

    assert result["action"] == "blocked"
    assert "duplicate" in result["reason"]
    assert calls == [1]


def test_fingerprint_duplicate_is_rejected_without_execution(tmp_path):
    first = evidence(tmp_path)
    second = evidence(tmp_path)
    calls = []

    run_paper_entry(first, execute=True, owner="worker-a", broker_call=lambda: calls.append(1) or {"status": "accepted", "order_id": "1"})
    result = run_paper_entry(second, execute=True, owner="worker-a", broker_call=lambda: calls.append(2) or {"status": "accepted", "order_id": "2"})

    assert result["action"] == "blocked"
    assert calls == [1]


def test_held_or_open_order_is_blocked(tmp_path):
    for field in ("held", "open_order"):
        calls = []
        result = run_paper_entry(
            evidence(tmp_path, candidate_id=field, **{field: True}), execute=True,
            owner="worker-a", broker_call=lambda: calls.append(1),
        )
        assert result["action"] == "blocked"
        assert "already" in result["reason"] or "open" in result["reason"]
        assert calls == []


def test_lease_contention_blocks_and_releases_after_success(tmp_path):
    db = tmp_path / "autopilot.db"
    first = evidence(tmp_path, candidate_id="first")
    second = evidence(tmp_path, candidate_id="second", occ_symbol="AAPL261218P00200000", side="put")
    entered = []

    # Keep the first lease held while its executor is running.
    def blocking_order():
        entered.append(True)
        return {"status": "accepted", "order_id": "1"}

    assert run_paper_entry(first, execute=True, owner="worker-a", broker_call=blocking_order)["action"] == "accepted"
    assert entered == [True]
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM paper_autopilot_leases").fetchone()[0] == 0


def test_paper_only_refusal_never_executes(tmp_path):
    calls = []
    result = run_paper_entry(
        evidence(tmp_path, trading_enabled=True), execute=True, owner="worker-a",
        broker_call=lambda: calls.append(1),
    )

    assert result["action"] == "blocked"
    assert "paper" in result["reason"].lower()
    assert calls == []


def test_authoritative_state_checker_blocks_exact_held_or_open_occ(tmp_path):
    for state, expected in (({"validated": True, "held": True}, "held"),
                            ({"validated": True, "open_order": True}, "open order")):
        calls = []
        result = run_paper_entry(
            evidence(tmp_path, candidate_id=expected, state_checker=lambda state=state: state),
            execute=True, owner="worker-a", broker_call=lambda: calls.append(True),
        )
        assert result["action"] == "blocked"
        assert expected in result["reason"]
        assert calls == []


def test_unvalidated_account_state_fails_closed_before_broker(tmp_path):
    calls = []
    result = run_paper_entry(
        evidence(tmp_path, state_checker=lambda: (_ for _ in ()).throw(RuntimeError("state unavailable"))),
        execute=True, owner="worker-a", broker_call=lambda: calls.append(True),
    )
    assert result["action"] == "blocked"
    assert "state" in result["reason"]
    assert calls == []


def test_lease_heartbeat_prevents_expiry_during_broker_execution(tmp_path):
    first = evidence(tmp_path, candidate_id="heartbeat-first")
    second = evidence(tmp_path, candidate_id="heartbeat-second", occ_symbol="AAPL261218P00200000", side="put")
    entered = threading.Event()
    result = {}

    def slow_order():
        entered.set()
        time.sleep(1.5)
        return {"status": "accepted", "order_id": "heartbeat-order"}

    worker = threading.Thread(target=lambda: result.update(
        run_paper_entry(first, execute=True, owner="worker-a", broker_call=slow_order,
                        lease_ttl_seconds=1)))
    worker.start()
    assert entered.wait(2)
    time.sleep(1.1)
    contender = run_paper_entry(
        second, execute=True, owner="worker-b", broker_call=lambda: {"status": "accepted", "order_id": "must-not-place"},
        lease_ttl_seconds=1,
    )
    worker.join(3)
    assert contender["action"] == "blocked"
    assert result["action"] == "accepted"


def test_options_pilot_state_query_matches_exact_occ_payloads():
    class Backend:
        def list_positions(self):
            return [
                {"symbol": "AAPL261218C00200000", "qty": "0"},
                {"symbol": "AAPL261218C00200000", "qty": "-0.5"},
            ]

        def list_open_orders(self):
            return [{"symbol": "AAPL261218P00200000", "status": "open"}]

    assert validate_paper_option_state(Backend(), "AAPL261218C00200000") == {
        "validated": True, "held": True, "open_order": False,
    }
    assert validate_paper_option_state(Backend(), "AAPL261218P00200000") == {
        "validated": True, "held": False, "open_order": True,
    }


def test_negative_exact_occ_position_quantity_is_held():
    class Backend:
        def list_positions(self):
            return [{"symbol": "AAPL261218C00200000", "qty": "-1"}]

        def list_open_orders(self):
            return []

    assert validate_paper_option_state(Backend(), "AAPL261218C00200000")["held"] is True


def test_malformed_broker_result_is_terminal_unknown_reconciliation(tmp_path):
    result = run_paper_entry(
        evidence(tmp_path), execute=True,
        broker_call=lambda: {"status": "accepted"},
    )
    assert result["action"] == "unknown_reconciliation"
    assert result["reconciliation_required"] is True


def test_contradictory_or_terminal_failure_broker_result_never_accepts(tmp_path):
    results = (
        {"accepted": True, "status": "rejected", "order_id": "bad-1"},
        {"action": "buy_placed", "status": "failed", "order_id": "bad-2"},
        {"action": "accepted", "status": "cancelled", "order_id": "bad-3"},
        {"action": "rejected", "status": "accepted", "order_id": "bad-4"},
    )
    for index, payload in enumerate(results):
        result = run_paper_entry(
            evidence(tmp_path, candidate_id=f"contradictory-{index}",
                     occ_symbol=f"AAPL261218C0020{index:05d}"),
            execute=True, broker_call=lambda payload=payload: payload,
        )
        assert result["action"] == "unknown_reconciliation"
        assert result["reconciliation_required"] is True


def test_intent_marker_survives_restart_and_blocks_retry(tmp_path):
    first = evidence(tmp_path, candidate_id="crash-candidate")
    result = run_paper_entry(
        first, execute=True,
        broker_call=lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert result["action"] == "unknown_reconciliation"
    restarted = evidence(tmp_path, candidate_id="crash-candidate-retry", premium=999)
    retry = run_paper_entry(restarted, execute=True, broker_call=lambda: {
        "status": "accepted", "order_id": "must-not-submit",
    })
    assert retry["action"] == "unknown_reconciliation"
    assert retry["reconciliation_required"] is True


def test_lease_contention_returns_promptly(tmp_path):
    first = evidence(tmp_path, candidate_id="prompt-first")
    entered = threading.Event()
    release = threading.Event()

    def callback():
        entered.set()
        release.wait(3)
        return {"status": "accepted", "order_id": "prompt-order"}

    worker = threading.Thread(target=lambda: run_paper_entry(first, execute=True, broker_call=callback))
    worker.start()
    assert entered.wait(2)
    second = evidence(tmp_path, candidate_id="prompt-second", occ_symbol="AAPL261218P00200000", side="put")
    started = time.monotonic()
    contender = run_paper_entry(second, execute=True, broker_call=lambda: {
        "status": "accepted", "order_id": "must-not-submit",
    })
    elapsed = time.monotonic() - started
    release.set()
    worker.join(3)
    assert contender["action"] == "blocked"
    assert elapsed < 2


def test_unknown_broker_submission_is_terminal_reconciliation_state(tmp_path):
    result = run_paper_entry(
        evidence(tmp_path), execute=True,
        broker_call=lambda: (_ for _ in ()).throw(TimeoutError("submission status unknown")),
    )
    assert result["action"] == "unknown_reconciliation"
    assert result["reconciliation_required"] is True
    assert result["order"] == {}


def test_contender_cannot_enter_until_serialized_callback_finishes(tmp_path):
    first = evidence(tmp_path, candidate_id="serialized-first")
    second = evidence(tmp_path, candidate_id="serialized-second", occ_symbol="AAPL261218P00200000", side="put")
    entered = threading.Event()
    finish = threading.Event()
    first_result = {}
    second_result = {}

    def callback():
        entered.set()
        finish.wait(3)
        return {"status": "accepted", "order_id": "serialized-order"}

    worker = threading.Thread(target=lambda: first_result.update(
        run_paper_entry(first, execute=True, broker_call=callback)))
    worker.start()
    assert entered.wait(2)
    contender = threading.Thread(target=lambda: second_result.update(
        run_paper_entry(second, execute=True, broker_call=lambda: {"status": "accepted", "order_id": "wrong-order"})))
    contender.start()
    time.sleep(0.2)
    assert contender.is_alive()
    finish.set()
    worker.join(3)
    contender.join(3)
    assert first_result["action"] == "accepted"
    assert second_result["action"] == "accepted"
