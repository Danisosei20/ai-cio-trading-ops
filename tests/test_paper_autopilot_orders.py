from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from robinhood_tools.paper_autopilot import build_evidence, run_paper_entry


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
    return built


def test_exact_occ_duplicate_is_rejected_without_execution(tmp_path):
    first = evidence(tmp_path)
    second = evidence(tmp_path, candidate_id="candidate-2", premium=99)
    calls = []

    assert run_paper_entry(first, execute=True, owner="worker-a", broker_call=lambda: calls.append(1) or {"order_id": "1"})["action"] == "accepted"
    result = run_paper_entry(second, execute=True, owner="worker-a", broker_call=lambda: calls.append(2) or {"order_id": "2"})

    assert result["action"] == "blocked"
    assert "duplicate" in result["reason"]
    assert calls == [1]


def test_fingerprint_duplicate_is_rejected_without_execution(tmp_path):
    first = evidence(tmp_path)
    second = evidence(tmp_path)
    calls = []

    run_paper_entry(first, execute=True, owner="worker-a", broker_call=lambda: calls.append(1) or {"order_id": "1"})
    result = run_paper_entry(second, execute=True, owner="worker-a", broker_call=lambda: calls.append(2) or {"order_id": "2"})

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
        from robinhood_tools.paper_autopilot import claim_cycle
        assert not claim_cycle(db, "other-worker", datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc))
        entered.append(True)
        return {"order_id": "1"}

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
