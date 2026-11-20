"""Small durable seam for paper-options evidence and cycle coordination."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


_EVIDENCE_DEFAULTS: dict[str, Any] = {
    "symbol": None,
    "occ_symbol": None,
    "side": None,
    "quantity": None,
    "quote": None,
    "premium": None,
    "vwap": None,
    "ema": None,
    "poc": None,
    "supply_demand": None,
    "support_resistance": None,
    "regime": None,
    "iv": None,
    "liquidity": None,
    "earnings_date": None,
    "session_status": None,
    "position_status": None,
    "policy_version": None,
    "gate_results": None,
    "action": "screened",
    "reason": "",
}
_VOLATILE_FINGERPRINT_FIELDS = {
    "candidate_id", "observed_at_utc", "observed_at_et", "created_at", "updated_at",
    "action", "reason", "order", "slack",
}


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_json_value(item) for item in value), key=lambda item: repr(item))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("now must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def build_evidence(candidate: dict, *, now: datetime) -> dict:
    """Build a JSON-safe, complete pre-entry evidence record."""
    observed_utc = _timestamp(now)
    observed = now.astimezone(ZoneInfo("America/New_York"))
    evidence = dict(_EVIDENCE_DEFAULTS)
    evidence.update(_json_value(candidate))
    evidence["candidate_id"] = str(uuid.uuid4())
    evidence["observed_at_utc"] = observed_utc
    evidence["observed_at_et"] = observed.isoformat()
    return evidence


def candidate_fingerprint(evidence: dict) -> str:
    """Return a deterministic digest for the candidate's substantive evidence."""
    payload = {
        key: _json_value(value)
        for key, value in evidence.items()
        if key not in _VOLATILE_FINGERPRINT_FIELDS
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_evidence(root: Path, evidence: dict) -> Path:
    """Write one evidence artifact with replacement semantics, never partial output."""
    root = Path(root)
    directory = root / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{evidence['candidate_id']}.json"
    temporary = directory / f".{path.name}.tmp"
    payload = json.dumps(_json_value(evidence), indent=2, sort_keys=True) + "\n"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS paper_autopilot_leases (
            name TEXT PRIMARY KEY, owner TEXT NOT NULL, acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS paper_autopilot_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, evidence TEXT NOT NULL, action TEXT NOT NULL,
            reason TEXT NOT NULL, order_payload TEXT, created_at TEXT NOT NULL
        )"""
    )
    return connection


def claim_cycle(db_path: Path, owner: str, now: datetime, ttl_seconds: int = 900) -> bool:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    acquired = _timestamp(now)
    expires = _timestamp(now + timedelta(seconds=ttl_seconds))
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM paper_autopilot_leases WHERE name='entry_cycle' AND expires_at <= ?", (acquired,))
        try:
            connection.execute(
                "INSERT INTO paper_autopilot_leases(name, owner, acquired_at, expires_at) VALUES('entry_cycle', ?, ?, ?)",
                (owner, acquired, expires),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def release_cycle(db_path: Path, owner: str) -> None:
    with _connect(db_path) as connection:
        connection.execute("DELETE FROM paper_autopilot_leases WHERE name='entry_cycle' AND owner=?", (owner,))


def record_attempt(
    db_path: Path,
    evidence: dict,
    action: str,
    reason: str,
    order: dict | None = None,
) -> None:
    evidence_payload = _json_value(evidence)
    with _connect(db_path) as connection:
        connection.execute(
            """INSERT INTO paper_autopilot_attempts
               (candidate_id, fingerprint, evidence, action, reason, order_payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence["candidate_id"],
                candidate_fingerprint(evidence),
                json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")),
                action,
                reason,
                json.dumps(_json_value(order), sort_keys=True, separators=(",", ":")),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
