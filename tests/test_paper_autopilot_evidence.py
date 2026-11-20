from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from robinhood_tools.errors import PolicyViolation
from scripts.options_pilot import (
    collect_entry_evidence,
    persist_candidate_before_gate,
)


NOW = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)


def quote(as_of: datetime = NOW) -> dict:
    return {
        "occ_symbol": "NVDA261218C00200000",
        "bid": 3.10,
        "ask": 3.30,
        "reviewed_price": 3.30,
        "as_of": as_of,
        "source": "alpaca-options",
        "iv": 0.24,
        "open_interest": 1200,
        "volume": 500,
        "spread": 0.20,
    }


def analysis(as_of: datetime = NOW) -> dict:
    return {
        "as_of": as_of,
        "vwap": 198.5,
        "ema": {"ema_9": 197.8, "ema_21": 196.2},
        "poc": 197.0,
        "supply_demand": {"supply": [201.0], "demand": [194.0]},
        "support_resistance": {"support": [195.0], "resistance": [200.0]},
        "regime": "RISK_ON",
        "earnings_date": "2026-12-01",
        "session_status": "OPEN",
        "position_status": "flat",
        "policy_version": "paper-options-v1",
    }


def test_collect_entry_evidence_contains_complete_deterministic_snapshot():
    evidence = collect_entry_evidence("nvda", "call", quote(), analysis(), now=NOW)

    assert evidence["symbol"] == "NVDA"
    assert evidence["side"] == "call"
    for field in (
        "vwap", "ema", "poc", "supply_demand", "support_resistance", "regime",
        "iv", "liquidity", "earnings_date", "freshness", "policy_version",
    ):
        assert field in evidence
        assert evidence[field] not in (None, {}, "")
    assert evidence["freshness"]["quote"]["observed_at_utc"] == NOW.isoformat()
    assert evidence["freshness"]["analysis"]["observed_at_utc"] == NOW.isoformat()


def test_missing_evidence_is_rejected_before_any_broker_call(tmp_path):
    missing = analysis()
    del missing["poc"]
    called = []

    with pytest.raises(PolicyViolation, match="poc"):
        persist_candidate_before_gate(
            tmp_path / "autopilot.db",
            collect_entry_evidence("NVDA", "call", quote(), missing, now=NOW),
            gate=lambda: True,
            broker_call=lambda: called.append(True),
            now=NOW,
        )
    assert called == []


def test_stale_evidence_is_rejected_and_persisted_before_gate(tmp_path):
    stale = NOW - timedelta(minutes=6)
    db = tmp_path / "autopilot.db"
    evidence = collect_entry_evidence("NVDA", "call", quote(stale), analysis(), now=NOW)
    called = []

    with pytest.raises(PolicyViolation, match="stale"):
        persist_candidate_before_gate(
            db,
            evidence,
            gate=lambda: True,
            broker_call=lambda: called.append(True),
            now=NOW,
        )
    assert called == []
    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT action, reason FROM paper_autopilot_attempts"
        ).fetchone()
    assert row[0] == "blocked"
    assert "stale" in row[1]


def test_screened_candidate_and_downstream_gate_failure_persist_before_order(tmp_path):
    db = tmp_path / "autopilot.db"
    evidence = collect_entry_evidence("NVDA", "call", quote(), analysis(), now=NOW)
    called = []

    result = persist_candidate_before_gate(
        db,
        evidence,
        gate=lambda: (False, "risk gate failed"),
        broker_call=lambda: called.append(True),
        now=NOW,
    )

    assert result["action"] == "blocked"
    assert called == []
    with sqlite3.connect(db) as connection:
        rows = connection.execute(
            "SELECT action, reason FROM paper_autopilot_attempts ORDER BY id"
        ).fetchall()
    assert rows[0][0] == "screened"
    assert rows[1] == ("blocked", "risk gate failed")
