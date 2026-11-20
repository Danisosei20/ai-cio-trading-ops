from __future__ import annotations

import json
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from robinhood_tools.paper_autopilot import (
    build_evidence,
    candidate_fingerprint,
    claim_cycle,
    claim_candidate,
    record_attempt,
    release_cycle,
    write_evidence,
)


def candidate() -> dict:
    return {
        "symbol": "AAPL",
        "occ_symbol": "AAPL261218C00200000",
        "side": "call",
        "quantity": 2,
        "quote": {"bid": 3.1, "ask": 3.3, "reviewed_price": 3.2},
        "premium": 640.0,
        "vwap": 198.5,
        "ema": {"ema_9": 197.8, "ema_21": 196.2},
        "poc": 197.0,
        "supply_demand": {"supply": [201.0], "demand": [194.0]},
        "support_resistance": {"support": [195.0], "resistance": [200.0]},
        "regime": "risk_on",
        "iv": 0.24,
        "liquidity": {"spread": 0.0625, "open_interest": 1200, "volume": 500},
        "earnings_date": "2026-12-01",
        "session_status": "OPEN",
        "position_status": "flat",
        "policy_version": "paper-options-v1",
        "gate_results": {"risk": "pending"},
    }


def test_build_evidence_contains_complete_contract_and_utc_timestamps():
    now = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)
    evidence = build_evidence(candidate(), now=now)

    assert evidence["candidate_id"]
    assert evidence["observed_at_utc"] == now.isoformat()
    assert evidence["observed_at_et"]
    for field in (
        "symbol", "occ_symbol", "side", "quantity", "quote", "premium", "vwap",
        "ema", "poc", "supply_demand", "support_resistance", "regime", "iv",
        "liquidity", "earnings_date", "session_status", "position_status",
        "policy_version", "gate_results", "action", "reason",
    ):
        assert field in evidence


def test_candidate_fingerprint_is_stable_across_mapping_order_and_record_metadata():
    now = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)
    first = build_evidence(candidate(), now=now)
    second = build_evidence(dict(reversed(list(candidate().items()))), now=now + timedelta(seconds=1))

    assert candidate_fingerprint(first) == candidate_fingerprint(second)


def test_write_evidence_replaces_atomically_and_returns_json_artifact(tmp_path):
    evidence = build_evidence(candidate(), now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    path = write_evidence(tmp_path, evidence)

    assert path == tmp_path / "evidence" / f"{evidence['candidate_id']}.json"
    assert json.loads(path.read_text()) == evidence
    assert list(path.parent.glob(".*.tmp")) == []


def test_concurrent_atomic_writes_leave_one_complete_artifact(tmp_path):
    evidence = build_evidence(candidate(), now=datetime(2026, 11, 20, tzinfo=timezone.utc))

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: write_evidence(tmp_path, evidence), range(4)))

    assert {path.read_text() for path in paths} == {paths[0].read_text()}
    assert list((tmp_path / "evidence").glob("*.tmp")) == []
    assert list((tmp_path / "evidence").glob(".*.tmp")) == []


def test_write_evidence_rejects_path_traversal_candidate_id(tmp_path):
    evidence = build_evidence(candidate(), now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    evidence["candidate_id"] = "..\\outside"

    with pytest.raises(ValueError, match="candidate_id"):
        write_evidence(tmp_path, evidence)


def test_persisted_evidence_allowlists_candidate_fields_and_rejects_nonfinite_values(tmp_path):
    raw = candidate()
    raw["broker_api_key"] = "secret"
    evidence = build_evidence(raw, now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    path = write_evidence(tmp_path, evidence)
    assert "broker_api_key" not in json.loads(path.read_text())

    bad = candidate()
    bad["iv"] = math.nan
    with pytest.raises(ValueError, match="finite"):
        build_evidence(bad, now=datetime(2026, 11, 20, tzinfo=timezone.utc))


def test_timestamp_values_are_canonicalized_to_utc():
    raw = candidate()
    raw["quote"]["as_of"] = datetime(2026, 11, 20, 10, 30, tzinfo=timezone(timedelta(hours=-5)))
    evidence = build_evidence(raw, now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    assert evidence["quote"]["as_of"] == "2026-11-20T15:30:00+00:00"


def test_nested_credential_like_keys_are_rejected_before_persistence(tmp_path):
    raw = candidate()
    raw["quote"]["vendor"] = {"credentials": {"api_key": "secret"}}

    with pytest.raises(ValueError, match="credential"):
        build_evidence(raw, now=datetime(2026, 11, 20, tzinfo=timezone.utc))


def test_legacy_lease_schema_migrates_token_and_generation_columns(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE paper_autopilot_leases "
            "(name TEXT PRIMARY KEY, owner TEXT NOT NULL, acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO paper_autopilot_leases VALUES (?, ?, ?, ?)",
            ("entry_cycle", "old-worker", "2026-11-20T15:00:00+00:00", "2026-11-20T15:31:00+00:00"),
        )

    assert not claim_cycle(db, "new-worker", datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc))
    with sqlite3.connect(db) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_autopilot_leases)")}
        assert {"token", "generation"} <= columns


def test_sqlite_connections_are_closed_after_success_and_error(tmp_path):
    db = tmp_path / "closed.db"
    evidence = build_evidence(candidate(), now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    record_attempt(db, evidence, "blocked", "test")
    db.unlink()

    broken = tmp_path / "broken.db"
    with pytest.raises(sqlite3.IntegrityError):
        record_attempt(broken, {**evidence, "candidate_id": None}, "blocked", "test")
    broken.unlink()


def test_cycle_lease_is_exclusive_then_can_be_reclaimed_after_expiry(tmp_path):
    db = tmp_path / "autopilot.db"
    start = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)

    token_a = claim_cycle(db, "worker-a", start, ttl_seconds=60)
    assert token_a
    assert not claim_cycle(db, "worker-b", start + timedelta(seconds=1), ttl_seconds=60)
    token_b = claim_cycle(db, "worker-b", start + timedelta(seconds=61), ttl_seconds=60)
    assert token_b
    release_cycle(db, "worker-b", token_b)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM paper_autopilot_leases").fetchone()[0] == 0


def test_stale_lease_token_cannot_release_new_generation(tmp_path):
    db = tmp_path / "autopilot.db"
    start = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)
    token_a = claim_cycle(db, "worker-a", start, ttl_seconds=60)
    token_b = claim_cycle(db, "worker-b", start + timedelta(seconds=61), ttl_seconds=60)

    release_cycle(db, "worker-b", token_a)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT owner FROM paper_autopilot_leases").fetchone()[0] == "worker-b"
    release_cycle(db, "worker-b", token_b)


def test_candidate_claim_is_atomic_for_fingerprint_and_occ_identity(tmp_path):
    db = tmp_path / "autopilot.db"
    evidence = build_evidence(candidate(), now=datetime(2026, 11, 20, tzinfo=timezone.utc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim_candidate(db, evidence), range(2)))
    assert sorted(results) == [False, True]

    changed = dict(evidence)
    changed["candidate_id"] = "different-id"
    changed["premium"] = 700
    assert not claim_candidate(db, changed)

    other_contract = dict(evidence)
    other_contract["candidate_id"] = "different-id-2"
    other_contract["occ_symbol"] = "AAPL261218P00200000"
    assert claim_candidate(db, other_contract)


def test_record_attempt_persists_before_gate_result(tmp_path):
    db = tmp_path / "autopilot.db"
    evidence = build_evidence(candidate(), now=datetime(2026, 11, 20, tzinfo=timezone.utc))

    record_attempt(db, evidence, "blocked", "missing fresh IV evidence")

    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT candidate_id, action, reason, order_payload FROM paper_autopilot_attempts"
        ).fetchone()
    assert row[:3] == (evidence["candidate_id"], "blocked", "missing fresh IV evidence")
    assert json.loads(row[3]) is None


def test_record_attempt_scrubs_untrusted_metadata_and_order_credentials(tmp_path):
    db = tmp_path / "autopilot.db"
    evidence = build_evidence(candidate(), now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    evidence["broker_token"] = "do-not-store"

    record_attempt(
        db,
        evidence,
        "accepted",
        "gated",
        {"id": "order-1", "status": "accepted", "api_key": "do-not-store"},
    )

    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT evidence, order_payload FROM paper_autopilot_attempts"
        ).fetchone()
    assert "do-not-store" not in row[0]
    assert json.loads(row[1]) == {"id": "order-1", "status": "accepted"}


def test_record_attempt_upserts_one_row_and_keeps_json_audit_history(tmp_path):
    db = tmp_path / "autopilot.db"
    evidence = build_evidence(candidate(), now=datetime(2026, 11, 20, tzinfo=timezone.utc))

    record_attempt(db, evidence, "screened", "candidate screened")
    record_attempt(db, evidence, "accepted", "gates passed", {"order_id": "paper-1"})

    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT action, reason, order_payload, evidence FROM paper_autopilot_attempts"
        ).fetchone()
    assert row[0:3] == ("accepted", "gates passed", '{"order_id":"paper-1"}')
    history = json.loads(row[3])["audit_history"]
    assert [item["action"] for item in history] == ["screened", "accepted"]


def test_concurrent_record_attempts_upsert_one_unique_fingerprint_row(tmp_path):
    db = tmp_path / "autopilot.db"
    evidence = build_evidence(candidate(), now=datetime(2026, 11, 20, tzinfo=timezone.utc))

    def write_once(_):
        record_attempt(db, evidence, "screened", "candidate screened")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write_once, range(8)))

    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM paper_autopilot_attempts").fetchone()[0] == 1
        indexes = connection.execute("PRAGMA index_list(paper_autopilot_attempts)").fetchall()
    assert any(index[2] and "fingerprint" in index[1] for index in indexes)
