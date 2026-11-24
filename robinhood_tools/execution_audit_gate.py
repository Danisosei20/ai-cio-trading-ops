#!/usr/bin/env python3
"""Fail-closed audit availability gate and durable submission-intent state.

Security-critical risk-increasing entries require a healthy, verifiable audit
store. Exits and reconciliation remain available when audit is degraded.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_REL = "outputs/paper/authorization_audit.jsonl"
ALERT_REL = "outputs/paper/authorization_audit_alerts.jsonl"
DB_REL = "outputs/paper/execution_authorizations.sqlite"


class AuditUnavailable(RuntimeError):
    """Structured security failure when audit durability cannot be proven."""


def audit_health(root: Path) -> tuple[bool, str]:
    target = Path(root) / AUDIT_REL
    if not target.exists():
        return False, "audit store missing"
    try:
        from .authorization_audit import verify_chain
        if not verify_chain(target):
            return False, "audit chain invalid"
    except Exception as exc:  # noqa: BLE001
        return False, f"audit verification error: {type(exc).__name__}"
    if not os.access(str(target.parent), os.W_OK):
        return False, "audit directory not writable"
    try:
        with target.open("a", encoding="utf-8") as fh:
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        return False, f"audit store not writable: {type(exc).__name__}"
    return True, "ok"


def emit_alert(root: Path, event: str, reason: str) -> dict:
    target = Path(root) / ALERT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    row = {"event_id": "alert-" + uuid.uuid4().hex[:16],
           "timestamp": datetime.now(timezone.utc).isoformat(),
           "event": event, "reason": str(reason)[:300]}
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def require_audit_healthy(root: Path) -> None:
    """Block a risk-increasing entry unless audit is healthy; alert on failure."""
    ok, reason = audit_health(root)
    if not ok:
        emit_alert(root, "audit_unavailable", reason)
        raise AuditUnavailable(f"audit store unavailable: {reason}")


def _conn(root: Path) -> sqlite3.Connection:
    p = Path(root) / DB_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10, isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS submission_intents ("
                 "authorization_id TEXT PRIMARY KEY, state TEXT NOT NULL,"
                 " symbol TEXT, side TEXT, quantity REAL, notional REAL,"
                 " broker_order_id TEXT, created_at TEXT, updated_at TEXT)")
    return conn


def open_intent(root: Path, auth_id: str, symbol: str, side: str,
                quantity: float, notional: float) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    row = {"authorization_id": auth_id, "state": "INTENDED", "symbol": symbol,
           "side": side, "quantity": quantity, "notional": notional,
           "broker_order_id": None, "created_at": now, "updated_at": now}
    conn = _conn(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO submission_intents"
                     "(authorization_id,state,symbol,side,quantity,notional,"
                     "broker_order_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (auth_id, "INTENDED", symbol, side, quantity, notional, None,
                      now, now))
        conn.commit()
    finally:
        conn.close()
    return row


def _transition(root: Path, auth_id: str, state: str, **fields: Any) -> None:
    conn = _conn(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        sets = ["state=?", "updated_at=?"]
        values: list[Any] = [state, datetime.now(timezone.utc).isoformat()]
        for key, value in fields.items():
            if key in {"broker_order_id"}:
                sets.append(f"{key}=?")
                values.append(value)
        values.append(auth_id)
        conn.execute(f"UPDATE submission_intents SET {','.join(sets)} WHERE authorization_id=?",
                     values)
        conn.commit()
    finally:
        conn.close()


def mark_submitted(root: Path, auth_id: str, broker_order_id: str) -> None:
    _transition(root, auth_id, "SUBMITTED", broker_order_id=broker_order_id)


def mark_uncertain(root: Path, auth_id: str) -> None:
    _transition(root, auth_id, "UNCERTAIN")


def mark_reconciled(root: Path, auth_id: str, outcome: str) -> None:
    _transition(root, auth_id, f"RECONCILED_{str(outcome).upper()}")


def pending_reconciliation(root: Path) -> list[dict]:
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT authorization_id,symbol,side,quantity,notional,broker_order_id,state"
            " FROM submission_intents WHERE state IN ('SUBMITTED','UNCERTAIN')").fetchall()
    finally:
        conn.close()
    return [dict(zip(("authorization_id", "symbol", "side", "quantity",
                      "notional", "broker_order_id", "state"), r)) for r in rows]
