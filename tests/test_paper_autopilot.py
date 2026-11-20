from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from robinhood_tools.paper_autopilot import (
    build_evidence,
    candidate_fingerprint,
    claim_cycle,
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


def test_cycle_lease_is_exclusive_then_can_be_reclaimed_after_expiry(tmp_path):
    db = tmp_path / "autopilot.db"
    start = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)

    assert claim_cycle(db, "worker-a", start, ttl_seconds=60)
    assert not claim_cycle(db, "worker-b", start + timedelta(seconds=1), ttl_seconds=60)
    assert claim_cycle(db, "worker-b", start + timedelta(seconds=61), ttl_seconds=60)
    release_cycle(db, "worker-b")
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM paper_autopilot_leases").fetchone()[0] == 0


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
