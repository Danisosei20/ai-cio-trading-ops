"""Small durable seam for paper-options evidence and cycle coordination."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import time
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
_ORDER_FIELDS = frozenset({"id", "order_id", "status", "side", "symbol", "occ_symbol", "quantity", "price", "submitted_at"})


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_json_value(item) for item in value), key=lambda item: repr(item))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON values must be finite")
    return value


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("now must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def build_evidence(candidate: dict, *, now: datetime) -> dict:
    """Build a JSON-safe, complete pre-entry evidence record."""
    observed_utc = _timestamp(now)
    observed = now.astimezone(ZoneInfo("America/New_York"))
    normalized = _json_value(candidate)
    evidence = {
        key: normalized[key] if key in normalized else default
        for key, default in _EVIDENCE_DEFAULTS.items()
    }
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
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _persisted_evidence(evidence: dict) -> dict:
    allowed = set(_EVIDENCE_DEFAULTS) | {"candidate_id", "observed_at_utc", "observed_at_et"}
    return _json_value({key: value for key, value in evidence.items() if key in allowed})


def _persisted_order(order: dict | None) -> dict | None:
    if order is None:
        return None
    return _json_value({key: value for key, value in order.items() if key in _ORDER_FIELDS})


def write_evidence(root: Path, evidence: dict) -> Path:
    """Write one evidence artifact with replacement semantics, never partial output."""
    root = Path(root)
    directory = root / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        candidate_id = str(uuid.UUID(str(evidence["candidate_id"])))
    except (KeyError, ValueError, AttributeError, TypeError) as exc:
        raise ValueError("candidate_id must be a UUID") from exc
    path = directory / f"{candidate_id}.json"
    payload = json.dumps(_persisted_evidence(evidence), indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary_name: str | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{candidate_id}.", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(20):
            try:
                os.replace(temporary_name, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.01)
        temporary_name = None
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return path


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS paper_autopilot_leases (
            name TEXT PRIMARY KEY, owner TEXT NOT NULL, token TEXT NOT NULL,
            acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS paper_autopilot_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, evidence TEXT NOT NULL, action TEXT NOT NULL,
            reason TEXT NOT NULL, order_payload TEXT, created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS paper_autopilot_candidate_claims (
            fingerprint TEXT PRIMARY KEY, occ_symbol TEXT NOT NULL UNIQUE,
            candidate_id TEXT NOT NULL, claimed_at TEXT NOT NULL
        )"""
    )
    return connection


def claim_cycle(db_path: Path, owner: str, now: datetime, ttl_seconds: int = 900) -> str | None:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    acquired = _timestamp(now)
    expires = _timestamp(now + timedelta(seconds=ttl_seconds))
    token = str(uuid.uuid4())
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM paper_autopilot_leases WHERE name='entry_cycle' AND expires_at <= ?", (acquired,))
        try:
            connection.execute(
                "INSERT INTO paper_autopilot_leases(name, owner, token, acquired_at, expires_at) VALUES('entry_cycle', ?, ?, ?, ?)",
                (owner, token, acquired, expires),
            )
        except sqlite3.IntegrityError:
            return False
    return token


def release_cycle(db_path: Path, owner: str, token: str) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            "DELETE FROM paper_autopilot_leases WHERE name='entry_cycle' AND owner=? AND token=?",
            (owner, token),
        )


def claim_candidate(db_path: Path, evidence: dict) -> bool:
    """Atomically reserve a fingerprint and exact OCC contract identity."""
    fingerprint = candidate_fingerprint(evidence)
    occ_symbol = str(evidence.get("occ_symbol") or "").strip()
    if not occ_symbol:
        raise ValueError("occ_symbol is required for duplicate claims")
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """INSERT INTO paper_autopilot_candidate_claims
                   (fingerprint, occ_symbol, candidate_id, claimed_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    fingerprint,
                    occ_symbol,
                    evidence["candidate_id"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def record_attempt(
    db_path: Path,
    evidence: dict,
    action: str,
    reason: str,
    order: dict | None = None,
) -> None:
    evidence_payload = _persisted_evidence(evidence)
    with _connect(db_path) as connection:
        connection.execute(
            """INSERT INTO paper_autopilot_attempts
               (candidate_id, fingerprint, evidence, action, reason, order_payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence["candidate_id"],
                candidate_fingerprint(evidence),
                json.dumps(evidence_payload, sort_keys=True, separators=(",", ":"), allow_nan=False),
                action,
                reason,
                json.dumps(_persisted_order(order), sort_keys=True, separators=(",", ":"), allow_nan=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
