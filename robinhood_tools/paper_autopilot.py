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
    "evidence_type", "audit_history", "order", "slack", "state_checker", "_broker_call",
    "database_path", "db_path", "held", "position_held", "held_positions", "open_order",
    "open_order_exists", "open_orders", "trading_enabled", "paper_only", "mode", "settings",
}
_ORDER_FIELDS = frozenset({"id", "order_id", "status", "side", "symbol", "occ_symbol", "quantity", "price", "submitted_at"})
_STATUS_PRECEDENCE = {
    "screened": 10, "blocked": 20, "intent": 30, "accepted": 40,
    "unknown_submitted": 50, "unknown_reconciliation": 50,
}
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


def _valid_broker_result(order: Any) -> bool:
    """Require a coherent success signal and a durable broker order identity."""
    if not isinstance(order, dict):
        return False
    status = str(order.get("status", "")).strip().lower()
    action = str(order.get("action", "")).strip().lower()
    rejected = {"rejected", "failed", "cancelled", "canceled", "error", "denied"}
    if status in rejected or action in rejected:
        return False
    success_signals = {"accepted", "placed", "buy_placed"}
    has_success_signal = (
        order.get("accepted") is True
        or status in success_signals
        or action in success_signals
    )
    if not has_success_signal:
        return False
    if status in {"accepted", "placed"} and action in rejected:
        return False
    if action in {"accepted", "placed", "buy_placed"} and status in rejected:
        return False
    return bool(str(order.get("order_id") or order.get("id") or "").strip())


def _event_key(event: dict) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _merge_history(histories: list[list[dict]]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for history in histories:
        for event in history:
            key = _event_key(event)
            if key not in seen:
                seen.add(key)
                merged.append(event)
    return merged


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
        histories = []
        for row in rows:
            row_evidence = json.loads(row[2])
            row_history = row_evidence.get("audit_history", [])
            if row_history:
                histories.append(row_history)
            else:
                histories.append([{
                    "action": row[3], "reason": row[5],
                    "order": json.loads(row[6]) if row[6] else None,
                    "at": row[7],
                }])
        merged["audit_history"] = _merge_history(histories)
        effective = max(
            enumerate(rows),
            key=lambda item: (_STATUS_PRECEDENCE.get(item[1][4] or item[1][3], 0), item[0]),
        )[1]
        effective_action = effective[3]
        effective_order = effective[6]
        if effective_action == "accepted" and not effective_order:
            effective_order = next((row[6] for row in reversed(rows) if row[3] == "accepted" and row[6]), None)
        merged.update({"action": effective_action, "status": effective[4], "reason": effective[5]})
        connection.execute(
            """UPDATE paper_autopilot_attempts
               SET evidence=?, action=?, status=?, reason=?, order_payload=?
               WHERE id=?""",
            (
                json.dumps(merged, sort_keys=True, separators=(",", ":"), allow_nan=False),
                effective_action, effective[4], effective[5], effective_order, keep[0],
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


def _connect(db_path: Path, *, timeout: float = 10, busy_timeout_ms: int = 10000) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
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
        connection.execute(
            "UPDATE paper_autopilot_attempts SET status=action "
            "WHERE status='screened' AND action <> 'screened'"
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


def _begin_serialized_cycle(db_path: Path, owner: str, now: datetime) -> tuple[sqlite3.Connection, str] | None:
    """Acquire the cycle row and retain SQLite's write transaction as the fence.

    The returned connection must stay open until the external callback returns.
    A contender's ``BEGIN IMMEDIATE`` therefore waits on this transaction and
    cannot observe/acquire the cycle claim while the callback is running.
    """
    # Keep coordination in a sidecar database. Holding SQLite's write
    # transaction on the application database would deadlock the normal
    # attempt/evidence writes performed around the callback.
    coordination_path = Path(db_path).with_name(Path(db_path).name + ".cycle")
    try:
        connection = _connect(coordination_path, timeout=0.2, busy_timeout_ms=200)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            return None
        raise
    token = str(uuid.uuid4())
    acquired = _timestamp(now)
    try:
        connection.execute("BEGIN IMMEDIATE")
        generation = connection.execute(
            "SELECT COALESCE(MAX(generation), 0) + 1 FROM paper_autopilot_leases"
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM paper_autopilot_leases WHERE name='entry_cycle' AND expires_at <= ?",
            (acquired,),
        )
        connection.execute(
            "INSERT INTO paper_autopilot_leases(name, owner, token, generation, acquired_at, expires_at) "
            "VALUES('entry_cycle', ?, ?, ?, ?, ?)",
            (owner, token, generation, acquired, _timestamp(now + timedelta(days=3650))),
        )
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        connection.rollback()
        connection.close()
        if isinstance(exc, sqlite3.OperationalError) and "locked" not in str(exc).lower():
            raise
        return None
    return connection, token


def _finish_serialized_cycle(connection: sqlite3.Connection) -> None:
    """Release a held execution claim after the callback has finished."""
    try:
        connection.execute("DELETE FROM paper_autopilot_leases WHERE name='entry_cycle'")
        connection.commit()
    finally:
        connection.close()


def release_cycle(db_path: Path, owner: str, token: str) -> None:
    with _managed_connection(db_path) as connection:
        connection.execute(
            "DELETE FROM paper_autopilot_leases WHERE name='entry_cycle' AND owner=? AND token=?",
            (owner, token),
        )


def renew_cycle(db_path: Path, owner: str, token: str, now: datetime, ttl_seconds: int = 900) -> bool:
    """Extend a lease only when its owner and fencing token still match."""
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    acquired = _timestamp(now)
    expires = _timestamp(now + timedelta(seconds=ttl_seconds))
    with _managed_connection(db_path) as connection:
        cursor = connection.execute(
            "UPDATE paper_autopilot_leases SET expires_at=? "
            "WHERE name='entry_cycle' AND owner=? AND token=? AND expires_at>?",
            (expires, owner, token, acquired),
        )
        return cursor.rowcount == 1


def lease_is_current(db_path: Path, owner: str, token: str, now: datetime) -> bool:
    """Check the fencing token immediately before entering an external side effect."""
    with _managed_connection(db_path) as connection:
        return connection.execute(
            "SELECT 1 FROM paper_autopilot_leases "
            "WHERE name='entry_cycle' AND owner=? AND token=? AND expires_at>?",
            (owner, token, _timestamp(now)),
        ).fetchone() is not None


def claim_candidate(db_path: Path, evidence: dict) -> bool:
    """Atomically reserve a fingerprint and exact OCC contract identity."""
    fingerprint = candidate_fingerprint(evidence)
    occ_symbol = str(evidence.get("occ_symbol") or "").strip().upper()
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
            "SELECT id, candidate_id, evidence, action, status, reason, order_payload "
            "FROM paper_autopilot_attempts WHERE fingerprint=? LIMIT 1",
            (fingerprint,),
        ).fetchone()
        history = []
        if existing:
            evidence["candidate_id"] = existing[1]
            existing_evidence = json.loads(existing[2])
            history = existing_evidence.get("audit_history", [])
            current_action = existing[4] or existing[3]
            current_precedence = _STATUS_PRECEDENCE.get(current_action, 0)
            incoming_precedence = _STATUS_PRECEDENCE.get(action, 0)
            preserve_existing = current_precedence >= incoming_precedence
            effective_action = current_action if preserve_existing else action
            effective_reason = existing[5] if preserve_existing else reason
            effective_order = existing[6] if preserve_existing else json.dumps(
                _persisted_order(order), sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            if current_action == "accepted" and not effective_order and existing[6]:
                effective_order = existing[6]
        else:
            effective_action = action
            effective_reason = reason
            effective_order = json.dumps(_persisted_order(order), sort_keys=True, separators=(",", ":"), allow_nan=False)
        event = {
            "action": action,
            "reason": reason,
            "order": _persisted_order(order),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        history.append(event)
        if existing and preserve_existing:
            evidence.clear()
            evidence.update(existing_evidence)
            evidence["audit_history"] = history
        else:
            evidence.update({
                "action": effective_action, "status": effective_action,
                "reason": effective_reason, "audit_history": history,
            })
        evidence_payload = _persisted_evidence(evidence)
        order_payload = effective_order
        encoded_evidence = json.dumps(evidence_payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if existing:
            connection.execute(
                """UPDATE paper_autopilot_attempts
                   SET candidate_id=?, evidence=?, action=?, status=?, reason=?, order_payload=?, created_at=?
                   WHERE id=?""",
                (evidence["candidate_id"], encoded_evidence, effective_action, effective_action, effective_reason, order_payload,
                 datetime.now(timezone.utc).isoformat(), existing[0]),
            )
        else:
            connection.execute(
                """INSERT INTO paper_autopilot_attempts
                   (candidate_id, fingerprint, evidence, action, status, reason, order_payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (evidence["candidate_id"], fingerprint, encoded_evidence, effective_action, effective_action, effective_reason,
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


def _attempt_action(db_path: Path, evidence: dict) -> str | None:
    fingerprint = candidate_fingerprint(evidence)
    occ_symbol = str(evidence.get("occ_symbol") or "").strip().upper()
    with _managed_connection(db_path) as connection:
        row = connection.execute(
            "SELECT action FROM paper_autopilot_attempts WHERE fingerprint=? "
            "UNION ALL SELECT a.action FROM paper_autopilot_attempts a "
            "JOIN paper_autopilot_candidate_claims c ON c.fingerprint=a.fingerprint "
            "WHERE c.occ_symbol=? LIMIT 1",
            (fingerprint, occ_symbol),
        ).fetchone()
    return row[0] if row else None


def run_paper_entry(
    evidence: dict,
    execute: bool = False,
    owner: str = "paper-autopilot",
    broker_call=None,
    *,
    db_path: Path | None = None,
    now: datetime | None = None,
    lease_ttl_seconds: int = 900,
    gate=None,
    evidence_root: Path | None = None,
    state_checker=None,
) -> dict:
    """Coordinate one duplicate-safe paper entry attempt.

    ``broker_call`` is deliberately injected by the caller so this seam cannot
    manufacture an order or bypass ``place_long_option``.  The production
    options flow supplies that callback after it has constructed its existing
    paper review/ledger path; tests can supply a small recording callback.
    Private orchestration values in ``evidence`` are never persisted.
    """
    path = Path(db_path or evidence.get("database_path") or evidence.get("db_path") or "outputs/paper/cio.db")
    observed = now
    if observed is None:
        raw_now = evidence.get("observed_at_utc")
        observed = datetime.fromisoformat(raw_now) if raw_now else datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("now must include a timezone")

    def blocked(reason: str, *, duplicate: bool = False, failure_stage: str | None = None) -> dict:
        evidence.update({"action": "blocked", "status": "blocked", "reason": reason,
                         "evidence_type": "blocked"})
        if failure_stage:
            evidence["failure_stage"] = failure_stage
        if evidence.get("known_fields") is None:
            fields = set(_EVIDENCE_DEFAULTS) - {"action", "status", "reason", "failure_stage",
                                               "known_fields", "unknown_fields", "evidence_type",
                                               "audit_history", "rejection_reasons", "gate_results"}
            known = sorted(field for field in fields if evidence.get(field) not in (None, {}, ""))
            evidence["known_fields"] = known
            evidence["unknown_fields"] = sorted(fields - set(known))
        record_attempt(path, evidence, "blocked", reason)
        if evidence_root is not None:
            write_evidence(Path(evidence_root), evidence)
        result = {"action": "blocked", "reason": reason, "candidate_id": evidence.get("candidate_id")}
        if duplicate:
            result["duplicate"] = True
        return result

    def unknown_reconciliation(reason: str, order: dict | None = None) -> dict:
        evidence.update({"action": "unknown_reconciliation", "status": "unknown_reconciliation",
                         "reason": reason, "evidence_type": "unknown_reconciliation",
                         "failure_stage": "broker", "reconciliation_required": True})
        if evidence.get("known_fields") is None:
            fields = set(_EVIDENCE_DEFAULTS) - {
                "action", "status", "reason", "failure_stage", "known_fields",
                "unknown_fields", "evidence_type", "audit_history", "rejection_reasons",
                "gate_results",
            }
            known = sorted(field for field in fields if evidence.get(field) not in (None, {}, ""))
            evidence["known_fields"] = known
            evidence["unknown_fields"] = sorted(fields - set(known))
        record_attempt(path, evidence, "unknown_reconciliation", reason, order or {})
        if evidence_root is not None:
            write_evidence(Path(evidence_root), evidence)
        return {"action": "unknown_reconciliation", "status": "unknown_reconciliation",
                "reason": reason, "candidate_id": evidence.get("candidate_id"),
                "order": order or {}, "reconciliation_required": True}

    settings = evidence.get("settings")
    settings_live = bool(getattr(settings, "trading_enabled", False)) if settings is not None else False
    settings_paper_disabled = settings is not None and getattr(settings, "paper_trading_enabled", True) is False
    if evidence.get("trading_enabled") is True or settings_live or settings_paper_disabled or evidence.get("paper_only") is False:
        return blocked("paper-only refusal: live trading is enabled or paper mode is disabled")
    if str(evidence.get("mode", "")).lower() == "live":
        return blocked("paper-only refusal: live mode is not accepted")
    if evidence.get("held") or evidence.get("position_held"):
        return blocked("candidate already held; entry refused")
    held_symbols = {str(item).upper() for item in (evidence.get("held_positions") or [])}
    open_symbols = {str(item).upper() for item in (evidence.get("open_orders") or [])}
    occ = str(evidence.get("occ_symbol") or "").upper()
    if occ in held_symbols:
        return blocked("candidate already held; entry refused")
    if occ in open_symbols:
        return blocked("matching open order already exists; entry refused")
    if evidence.get("open_order") or evidence.get("open_order_exists"):
        return blocked("matching open order already exists; entry refused")
    position_status = str(evidence.get("position_status") or "").lower()
    if position_status in {"held", "open", "long", "pending", "buy_pending"}:
        return blocked(f"candidate position status is {position_status}; entry refused")

    prior_action = _attempt_action(path, evidence)
    if prior_action in {"intent", "unknown_submitted", "unknown_reconciliation"}:
        return unknown_reconciliation("prior broker execution requires reconciliation")

    serialized_claim = _begin_serialized_cycle(path, owner, observed)
    if not serialized_claim:
        return blocked("entry cycle lease is held by another worker")
    cycle_connection, token = serialized_claim
    try:
        if execute:
            state_checker = state_checker or evidence.get("state_checker")
            if not callable(state_checker):
                return blocked("authoritative paper position/order state is unavailable", failure_stage="state")
            try:
                state = state_checker()
            except Exception as exc:
                return blocked(f"paper position/order state validation failed: {exc}", failure_stage="state")
            if not isinstance(state, dict) or state.get("validated") is not True:
                return blocked("paper position/order state validation failed", failure_stage="state")
            if state.get("held"):
                return blocked("candidate already held; entry refused", failure_stage="state")
            if state.get("open_order"):
                return blocked("matching open order already exists; entry refused", failure_stage="state")
        if not claim_candidate(path, evidence):
            return blocked("duplicate candidate or OCC claim", duplicate=True)
        evidence.update({"action": "screened", "status": "screened", "reason": "candidate screened"})
        record_attempt(path, evidence, "screened", "candidate screened")
        if evidence_root is not None:
            write_evidence(Path(evidence_root), evidence)
        if gate is not None:
            try:
                decision = gate()
            except Exception as exc:
                blocked(str(exc) or type(exc).__name__, failure_stage="gate")
                raise
            allowed, gate_reason = (decision, "gate failed") if isinstance(decision, bool) else decision
            if not allowed:
                return blocked(str(gate_reason), failure_stage="gate")
        if not execute:
            return {"action": "screened", "reason": "execution disabled", "candidate_id": evidence.get("candidate_id")}
        if broker_call is None:
            broker_call = evidence.get("_broker_call")
        if not callable(broker_call):
            return blocked("paper entry executor is not configured")
        # This durable intent is written before invoking the external side
        # effect. If the process dies at any point afterward, restart sees the
        # marker and cannot treat the screened candidate as retryable.
        evidence.update({"action": "intent", "status": "intent", "reason": "broker execution intent recorded"})
        record_attempt(path, evidence, "intent", "broker execution intent recorded")
        if evidence_root is not None:
            write_evidence(Path(evidence_root), evidence)
        try:
            order = broker_call()
        except BaseException as exc:
            # Once the external callback has been invoked, its submission
            # outcome is unknowable from an exception alone. Never turn this
            # into a retryable blocked result: persist a terminal state that
            # requires reconciliation before any retry.
            _finish_serialized_cycle(cycle_connection)
            cycle_connection = None
            return unknown_reconciliation(f"broker submission outcome unknown: {exc}")
        _finish_serialized_cycle(cycle_connection)
        cycle_connection = None
        if not _valid_broker_result(order):
            return unknown_reconciliation("broker returned an invalid accepted-order result", order if isinstance(order, dict) else {})
        evidence.update({"action": "accepted", "status": "accepted", "reason": "gates passed"})
        record_attempt(path, evidence, "accepted", "gates passed", order if isinstance(order, dict) else None)
        if evidence_root is not None:
            write_evidence(Path(evidence_root), evidence)
        return {"action": "accepted", "reason": "gates passed", "candidate_id": evidence.get("candidate_id"), "order": order}
    finally:
        if cycle_connection is not None:
            _finish_serialized_cycle(cycle_connection)
