from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timedelta, timezone

import pytest

from robinhood_tools.errors import PolicyViolation
from scripts.options_pilot import (
    collect_entry_evidence,
    persist_candidate_before_gate,
)


NOW = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)
def STATE_OK():
    return {"validated": True, "held": False, "open_order": False}


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
        "earnings_provenance": "SEC/company-calendar",
        "session_status": "OPEN",
        "position_status": "flat",
        "policy_version": "paper-options-v1",
        "source": "alpaca-bars",
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
            state_checker=STATE_OK,
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
            state_checker=STATE_OK,
        )
    assert called == []
    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT action, reason, evidence FROM paper_autopilot_attempts"
        ).fetchone()
    assert row[0] == "blocked"
    assert "stale" in row[1]
    blocked = json.loads(row[2])
    assert blocked["evidence_type"] == "blocked"
    assert blocked["failure_stage"] == "validation"
    assert blocked["known_fields"]
    assert blocked["unknown_fields"] == []
    assert blocked["quote"]["source"] == "alpaca-options"
    assert blocked["freshness"]["quote"]["observed_at_utc"]


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
        state_checker=STATE_OK,
    )

    assert result["action"] == "blocked"
    assert called == []
    with sqlite3.connect(db) as connection:
        rows = connection.execute(
            "SELECT action, reason FROM paper_autopilot_attempts ORDER BY id"
        ).fetchall()
    assert rows == [("blocked", "risk gate failed")]


@pytest.mark.parametrize("field,value", [("bid", 0), ("ask", 0), ("reviewed_price", 0), ("iv", -0.1), ("volume", 0)])
def test_structurally_invalid_quote_evidence_blocks(field, value, tmp_path):
    raw_quote = quote()
    raw_quote[field] = value
    evidence = collect_entry_evidence("NVDA", "put", raw_quote, analysis(), now=NOW)

    with pytest.raises(PolicyViolation, match="invalid"):
        persist_candidate_before_gate(
            tmp_path / "autopilot.db", evidence, gate=lambda: True,
            broker_call=lambda: pytest.fail("broker must not be called"), now=NOW,
            evidence_root=tmp_path,
            state_checker=STATE_OK,
        )
    assert list((tmp_path / "evidence").glob("*.json"))


def test_policy_version_mismatch_and_gate_exception_are_recorded(tmp_path):
    db = tmp_path / "autopilot.db"
    evidence = collect_entry_evidence("NVDA", "call", quote(), analysis(), now=NOW)
    with pytest.raises(PolicyViolation, match="policy version"):
        persist_candidate_before_gate(
            db, evidence, gate=lambda: True, broker_call=lambda: None, now=NOW,
            active_policy_version="desk-v2", evidence_root=tmp_path,
            state_checker=STATE_OK,
        )

    put_quote = quote()
    put_quote["occ_symbol"] = "NVDA261218P00200000"
    evidence = collect_entry_evidence("NVDA", "put", put_quote, analysis(), now=NOW)
    with pytest.raises(PolicyViolation, match="downstream failure"):
        persist_candidate_before_gate(
            db, evidence, gate=lambda: (_ for _ in ()).throw(PolicyViolation("downstream failure")),
            broker_call=lambda: pytest.fail("broker must not be called"), now=NOW,
            evidence_root=tmp_path,
            state_checker=STATE_OK,
        )
    with sqlite3.connect(db) as connection:
        rows = connection.execute("SELECT action, reason, evidence FROM paper_autopilot_attempts ORDER BY id").fetchall()
    assert rows[0][0] == "blocked"
    assert rows[1][:2] == ("blocked", "downstream failure")
    assert json.loads(rows[1][2])["failure_stage"] == "gate"


def test_order_callback_sees_screened_sqlite_and_json_before_invocation(tmp_path):
    db = tmp_path / "autopilot.db"
    put_quote = quote()
    put_quote["occ_symbol"] = "NVDA261218P00200000"
    evidence = collect_entry_evidence("NVDA", "put", put_quote, analysis(), now=NOW)
    observed = {}

    def broker_call():
        with sqlite3.connect(db) as connection:
            observed["sqlite"] = connection.execute(
                "SELECT action FROM paper_autopilot_attempts ORDER BY id"
            ).fetchall()
        artifacts = list((tmp_path / "evidence").glob("*.json"))
        observed["artifact"] = artifacts[0].read_text(encoding="utf-8")
        return {"order_id": "paper-1", "status": "accepted"}

    result = persist_candidate_before_gate(
        db, evidence, gate=lambda: True, broker_call=broker_call, now=NOW,
        evidence_root=tmp_path,
        state_checker=STATE_OK,
    )

    assert result["action"] == "accepted"
    assert observed["sqlite"] == [("intent",)]
    assert '"action": "intent"' in observed["artifact"]


def test_broker_exception_is_structured_unknown_submission(tmp_path):
    evidence = collect_entry_evidence("NVDA", "call", quote(), analysis(), now=NOW)
    result = persist_candidate_before_gate(
        tmp_path / "autopilot.db", evidence, gate=lambda: True,
        broker_call=lambda: (_ for _ in ()).throw(RuntimeError("paper broker down")),
        now=NOW, evidence_root=tmp_path, state_checker=STATE_OK,
    )
    assert result["action"] == "unknown_reconciliation"
    assert result["reconciliation_required"] is True
    artifact = json.loads(next((tmp_path / "evidence").glob("*.json")).read_text())
    assert artifact["evidence_type"] == "unknown_reconciliation"
    assert artifact["failure_stage"] == "broker"
    assert artifact["known_fields"]
    assert artifact["unknown_fields"] == []


def test_early_block_helper_writes_one_blocked_attempt_and_artifact(tmp_path):
    from scripts.options_pilot import record_preentry_block

    record_preentry_block(
        tmp_path / "autopilot.db", "NVDA", "call", quote(), {},
        reason="premium cap", failure_stage="premium_cap", now=NOW, evidence_root=tmp_path,
    )
    record_preentry_block(
        tmp_path / "autopilot.db", "NVDA", "call", quote(), {},
        reason="premium cap", now=NOW, evidence_root=tmp_path,
    )
    with sqlite3.connect(tmp_path / "autopilot.db") as connection:
        rows = connection.execute("SELECT action, reason FROM paper_autopilot_attempts").fetchall()
    assert rows == [("blocked", "premium cap")]
    assert len(list((tmp_path / "evidence").glob("*.json"))) == 1
    artifact = json.loads(next((tmp_path / "evidence").glob("*.json")).read_text())
    assert artifact["failure_stage"] == "premium_cap"
    assert "quote" in artifact["known_fields"]
    assert "vwap" in artifact["unknown_fields"]


def test_valid_candidate_claim_is_before_gate_and_duplicate_is_blocked(tmp_path):
    from scripts.options_pilot import persist_candidate_before_gate

    db = tmp_path / "autopilot.db"
    first = collect_entry_evidence("NVDA", "call", quote(), analysis(), now=NOW)
    second = collect_entry_evidence("NVDA", "call", quote(), analysis(), now=NOW)
    gate_calls = []
    first_result = persist_candidate_before_gate(
        db, first, gate=lambda: (gate_calls.append(True) or (False, "risk gate failed")),
        broker_call=lambda: pytest.fail("broker must not be called"), now=NOW,
        evidence_root=tmp_path, state_checker=STATE_OK,
    )
    second_result = persist_candidate_before_gate(
        db, second, gate=lambda: pytest.fail("duplicate must not reach gate"),
        broker_call=lambda: pytest.fail("broker must not be called"), now=NOW,
        evidence_root=tmp_path, state_checker=STATE_OK,
    )

    assert first_result["action"] == "blocked"
    assert second_result["reason"] == "duplicate candidate or OCC claim"
    assert gate_calls == [True]
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM paper_autopilot_attempts").fetchone()[0] == 1


def test_rejected_chain_candidates_are_persisted_individually(tmp_path):
    from types import SimpleNamespace
    from scripts.options_pilot import persist_rejected_chain_candidates

    rejected = [
        SimpleNamespace(option_symbol="NVDA261218C00200000", reasons=["spread too wide"]),
        SimpleNamespace(option_symbol="NVDA261218C00210000", reasons=["volume too low"]),
    ]
    persist_rejected_chain_candidates(
        tmp_path / "autopilot.db", "NVDA", "call", rejected, now=NOW,
        evidence_root=tmp_path,
    )
    with sqlite3.connect(tmp_path / "autopilot.db") as connection:
        rows = connection.execute(
            "SELECT action, reason FROM paper_autopilot_attempts ORDER BY id"
        ).fetchall()
    assert rows == [
        ("screened", "selection rejected: spread too wide"),
        ("screened", "selection rejected: volume too low"),
    ]


def test_raw_chain_universe_rejections_are_not_persisted(tmp_path):
    from types import SimpleNamespace
    from scripts.options_pilot import persist_rejected_chain_candidates

    rejected = [
        SimpleNamespace(option_symbol="NVDA261218P00200000",
                        reasons=["wrong right (put, want call)"]),
        SimpleNamespace(option_symbol="NVDA260101C00200000",
                        reasons=["DTE 1 outside 7-60"]),
        SimpleNamespace(option_symbol="NVDA261218C00210000",
                        reasons=["spread 20.0% too wide"]),
    ]
    persist_rejected_chain_candidates(
        tmp_path / "autopilot.db", "NVDA", "call", rejected, now=NOW,
        evidence_root=tmp_path,
    )
    with sqlite3.connect(tmp_path / "autopilot.db") as connection:
        rows = connection.execute(
            "SELECT action, reason FROM paper_autopilot_attempts ORDER BY id"
        ).fetchall()
    assert rows == [("screened", "selection rejected: spread 20.0% too wide")]


def test_all_rejected_chain_is_persisted_before_selection_exception(tmp_path):
    from types import SimpleNamespace
    from scripts.options_pilot import persist_rejected_chain_candidates

    rejected = [
        SimpleNamespace(option_symbol="NVDA261218C00200000", reasons=["DTE too short"]),
        SimpleNamespace(option_symbol="NVDA261218C00210000", reasons=["delta outside band"]),
    ]
    error = PolicyViolation("No suitable long call: 2 rejected.")
    error.rejected = rejected
    try:
        persist_rejected_chain_candidates(
            tmp_path / "autopilot.db", "NVDA", "call", error.rejected,
            now=NOW, evidence_root=tmp_path,
        )
        raise error
    except PolicyViolation:
        pass
    with sqlite3.connect(tmp_path / "autopilot.db") as connection:
        assert connection.execute("SELECT count(*) FROM paper_autopilot_attempts").fetchone()[0] == 2


@pytest.mark.parametrize("mutator,match", [
    (lambda q, a: q.update(source="untrusted"), "quote source"),
    (lambda q, a: a.update(source="untrusted"), "analysis source"),
    (lambda q, a: q.update(occ_symbol="NVDA261218P00200000"), "right"),
    (lambda q, a: q.update(occ_symbol="AAPL261218C00200000"), "underlying"),
    (lambda q, a: a.update(earnings_date="2026-12-01", earnings_provenance="unknown"), "provenance"),
])
def test_source_occ_and_semantic_validation_fail_closed(mutator, match, tmp_path):
    raw_quote, raw_analysis = quote(), analysis()
    mutator(raw_quote, raw_analysis)
    evidence = collect_entry_evidence("NVDA", "call", raw_quote, raw_analysis, now=NOW)

    with pytest.raises(PolicyViolation, match=match):
        persist_candidate_before_gate(
            tmp_path / "autopilot.db", evidence, gate=lambda: True,
            broker_call=lambda: pytest.fail("broker must not be called"), now=NOW,
        )


def test_quantity_consistency_fails_closed(tmp_path):
    evidence = collect_entry_evidence("NVDA", "call", quote(), analysis(), now=NOW)
    evidence["quantity"] = 2
    evidence["quote"]["quantity"] = 1
    with pytest.raises(PolicyViolation, match="quantity"):
        persist_candidate_before_gate(
            tmp_path / "autopilot.db", evidence, gate=lambda: True,
            broker_call=lambda: pytest.fail("broker must not be called"), now=NOW,
        )
