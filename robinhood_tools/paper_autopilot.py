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
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import re


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
    "earnings_provenance": None,
    "analysis_source": None,
    "session_status": None,
    "position_status": None,
    "policy_version": None,
    "freshness": None,
    "gate_results": None,
    "action": "screened",
    "status": "screened",
    "reason": "",
    "failure_stage": None,
    "known_fields": None,
    "unknown_fields": None,
    "evidence_type": "entry",
    "audit_history": [],
    "rejection_reasons": None,
}
_VOLATILE_FINGERPRINT_FIELDS = {
    "candidate_id", "observed_at_utc", "observed_at_et", "created_at", "updated_at",
    "action", "status", "reason", "failure_stage", "known_fields", "unknown_fields",
    "evidence_type", "audit_history", "order", "slack",
}
_ORDER_FIELDS = frozenset({"id", "order_id", "status", "side", "symbol", "occ_symbol", "quantity", "price", "submitted_at"})
_CREDENTIAL_KEY = re.compile(r"(?:api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|secret|token|password|passwd|credential|authorization|bearer)")


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            key_text = str(key)
            if _CREDENTIAL_KEY.search(key_text.lower().replace(" ", "_")):
                raise ValueError(f"credential-like key cannot be persisted: {key_text}")
            normalized[key_text] = _json_value(item)
        return normalized
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
    normalized = _json_value({key: value for key, value in candidate.items() if key in _EVIDENCE_DEFAULTS})
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


def _deduplicate_attempts(connection: sqlite3.Connection) -> None:
    """Collapse pre-unique-index duplicates without discarding transition history."""
    fingerprints = connection.execute(
        """SELECT fingerprint FROM paper_autopilot_attempts
           GROUP BY fingerprint HAVING count(*) > 1"""
    ).fetchall()
    for (fingerprint,) in fingerprints:
        rows = connection.execute(
            """SELECT id, candidate_id, evidence, action, status, reason,
                      order_payload, created_at
               FROM paper_autopilot_attempts WHERE fingerprint=? ORDER BY id""",
            (fingerprint,),
        ).fetchall()
        keep = rows[0]
        merged = json.loads(keep[2])
        history = list(merged.get("audit_history", []))
        for row in rows:
            row_evidence = json.loads(row[2])
            row_history = row_evidence.get("audit_history", [])
            if row_history:
                history.extend(row_history)
            else:
                history.append({
                    "action": row[3], "reason": row[5],
                    "order": json.loads(row[6]) if row[6] else None,
                    "at": row[7],
                })
        merged["audit_history"] = history
        latest = rows[-1]
        connection.execute(
            """UPDATE paper_autopilot_attempts
               SET evidence=?, action=?, status=?, reason=?, order_payload=?
               WHERE id=?""",
            (
                json.dumps(merged, sort_keys=True, separators=(",", ":"), allow_nan=False),
                latest[3], latest[4], latest[5], latest[6], keep[0],
            ),
        )
        connection.execute(
            "DELETE FROM paper_autopilot_attempts WHERE fingerprint=? AND id<>?",
            (fingerprint, keep[0]),
        )


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
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS paper_autopilot_leases (
                name TEXT PRIMARY KEY, owner TEXT NOT NULL, token TEXT NOT NULL,
                generation INTEGER NOT NULL, acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL
            )"""
        )
        lease_columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_autopilot_leases)")}
        if "token" not in lease_columns:
            connection.execute("ALTER TABLE paper_autopilot_leases ADD COLUMN token TEXT")
        if "generation" not in lease_columns:
            connection.execute("ALTER TABLE paper_autopilot_leases ADD COLUMN generation INTEGER")
        connection.execute("UPDATE paper_autopilot_leases SET token=COALESCE(token, ?), generation=COALESCE(generation, 0)", (str(uuid.uuid4()),))
        connection.execute(
            """CREATE TABLE IF NOT EXISTS paper_autopilot_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, evidence TEXT NOT NULL, action TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'screened', reason TEXT NOT NULL,
                order_payload TEXT, created_at TEXT NOT NULL
            )"""
        )
        attempt_columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_autopilot_attempts)")}
        if "status" not in attempt_columns:
            connection.execute(
                "ALTER TABLE paper_autopilot_attempts ADD COLUMN status TEXT NOT NULL DEFAULT 'screened'"
            )
        _deduplicate_attempts(connection)
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_autopilot_attempts_fingerprint "
            "ON paper_autopilot_attempts(fingerprint)"
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS paper_autopilot_candidate_claims (
                fingerprint TEXT PRIMARY KEY, occ_symbol TEXT NOT NULL UNIQUE,
                candidate_id TEXT NOT NULL, claimed_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_autopilot_claims_fingerprint "
            "ON paper_autopilot_candidate_claims(fingerprint)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_autopilot_claims_occ_symbol "
            "ON paper_autopilot_candidate_claims(occ_symbol)"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        connection.close()
        raise
    return connection


@contextmanager
def _managed_connection(db_path: Path):
    connection = _connect(db_path)
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        connection.close()


def claim_cycle(db_path: Path, owner: str, now: datetime, ttl_seconds: int = 900) -> str | None:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    acquired = _timestamp(now)
    expires = _timestamp(now + timedelta(seconds=ttl_seconds))
    token = str(uuid.uuid4())
    with _managed_connection(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM paper_autopilot_leases WHERE name='entry_cycle' AND expires_at <= ?", (acquired,))
        generation = connection.execute(
            "SELECT COALESCE(MAX(generation), 0) + 1 FROM paper_autopilot_leases"
        ).fetchone()[0]
        try:
            connection.execute(
                "INSERT INTO paper_autopilot_leases(name, owner, token, generation, acquired_at, expires_at) VALUES('entry_cycle', ?, ?, ?, ?, ?)",
                (owner, token, generation, acquired, expires),
            )
        except sqlite3.IntegrityError:
            return False
    return token


def release_cycle(db_path: Path, owner: str, token: str) -> None:
    with _managed_connection(db_path) as connection:
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
    with _managed_connection(db_path) as connection:
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
    with _managed_connection(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        fingerprint = candidate_fingerprint(evidence)
        existing = connection.execute(
            "SELECT id, candidate_id, evidence FROM paper_autopilot_attempts WHERE fingerprint=? LIMIT 1",
            (fingerprint,),
        ).fetchone()
        history = []
        if existing:
            evidence["candidate_id"] = existing[1]
            history = json.loads(existing[2]).get("audit_history", [])
        event = {
            "action": action,
            "reason": reason,
            "order": _persisted_order(order),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        history.append(event)
        evidence.update({"action": action, "status": action, "reason": reason, "audit_history": history})
        evidence_payload = _persisted_evidence(evidence)
        order_payload = json.dumps(_persisted_order(order), sort_keys=True, separators=(",", ":"), allow_nan=False)
        encoded_evidence = json.dumps(evidence_payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if existing:
            connection.execute(
                """UPDATE paper_autopilot_attempts
                   SET candidate_id=?, evidence=?, action=?, status=?, reason=?, order_payload=?, created_at=?
                   WHERE id=?""",
                (evidence["candidate_id"], encoded_evidence, action, action, reason, order_payload,
                 datetime.now(timezone.utc).isoformat(), existing[0]),
            )
        else:
            connection.execute(
                """INSERT INTO paper_autopilot_attempts
                   (candidate_id, fingerprint, evidence, action, status, reason, order_payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (evidence["candidate_id"], fingerprint, encoded_evidence, action, action, reason,
                 order_payload, datetime.now(timezone.utc).isoformat()),
            )


def attempt_exists(db_path: Path, evidence: dict, action: str, reason: str) -> bool:
    """Return whether this substantive attempt was already recorded."""
    fingerprint = candidate_fingerprint(evidence)
    with _managed_connection(db_path) as connection:
        return connection.execute(
            "SELECT 1 FROM paper_autopilot_attempts WHERE fingerprint=? AND action=? AND reason=? LIMIT 1",
            (fingerprint, action, reason),
        ).fetchone() is not None
