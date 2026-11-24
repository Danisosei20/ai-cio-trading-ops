#!/usr/bin/env python3
"""Deterministic, integrity-bound execution authorization (ADR 0002, DRAFT).

AI research never authorizes an order. This module turns an already-formed
deterministic gate decision into a bounded, account-bound, replay-resistant
authorization and atomically consumes it at execution time.

Enforcement is OFF unless ``EXECUTION_AUTHORIZATION_ENABLED=true`` (ADR 0002 is
not approved). A missing HMAC key is a hard failure, never a bypass.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KEY_ENV = "EXECUTION_AUTH_HMAC_KEY"
KEY_FILE = ".cio_execution_auth.key"
FLAG_ENV = "EXECUTION_AUTHORIZATION_ENABLED"
DB_NAME = "execution_authorizations.sqlite"
POLICY = "execution-auth-v1"

STATES = ("PENDING", "VALIDATED", "AUTHORIZED", "CONSUMED",
          "REJECTED", "EXPIRED", "REVOKED", "INVALIDATED")
TERMINAL = {"CONSUMED", "REJECTED", "EXPIRED", "REVOKED", "INVALIDATED"}
TRANSITIONS = {
    "PENDING": {"VALIDATED", "REJECTED"},
    "VALIDATED": {"AUTHORIZED", "REJECTED"},
    "AUTHORIZED": {"CONSUMED", "EXPIRED", "REVOKED", "INVALIDATED"},
}

REQUIRED_FIELDS = (
    "authorization_id", "decision_id", "gate_run_id", "strategy_id", "symbol",
    "instrument", "position_effect", "side", "max_quantity", "max_notional",
    "max_risk", "broker", "account_id", "environment", "artifact_hashes",
    "gate_verdict", "gate_generated_at", "issued_at", "expires_at", "state",
    "policy_version", "idempotency_key", "evidence_paths", "signature",
)


class AuthorizationError(ValueError):
    """Raised when an authorization cannot be created or is invalid."""


def enforcement_enabled() -> bool:
    """True only for the exact flag value 'true' (fail-closed default)."""
    return os.environ.get(FLAG_ENV, "") == "true"


def _hmac_key() -> bytes | None:
    value = os.environ.get(KEY_ENV, "").strip()
    if value:
        return value.encode("utf-8")
    try:
        path = Path.home() / KEY_FILE
        if path.is_file():
            raw = path.read_bytes()
            return raw if raw else None
    except (OSError, RuntimeError):
        return None
    return None


def canonical_bytes(obj: dict) -> bytes:
    """Versioned canonical serialization: sorted keys, compact separators."""
    clean = {k: v for k, v in obj.items() if k != "signature"}
    return json.dumps({"v": 1, **clean}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _sign(record: dict) -> dict:
    key = _hmac_key()
    if key is None:
        raise AuthorizationError("authorization signing key unavailable")
    signed = dict(record)
    signed["signature"] = hmac.new(key, canonical_bytes(signed), hashlib.sha256).hexdigest()
    return signed


def verify_integrity(record: Any) -> bool:
    key = _hmac_key()
    if key is None or not isinstance(record, dict):
        return False
    signature = record.get("signature")
    if not isinstance(signature, str) or not signature:
        return False
    expected = hmac.new(key, canonical_bytes(record), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def artifact_hashes(paths: list[Path], *, root: Path) -> dict[str, str]:
    """Stable SHA-256 of each evidence artifact, keyed by repo-relative path."""
    out: dict[str, str] = {}
    for path in paths:
        p = Path(path)
        if not p.is_absolute():
            p = root / p
        if not p.is_file():
            raise AuthorizationError(f"evidence artifact missing: {p}")
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = str(p).replace("\\", "/")
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    if not out:
        raise AuthorizationError("no evidence artifacts")
    return out


def _parse_stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def create_authorization(*, decision: dict, root: Path, artifacts: list[Path],
                         strategy_id: str, symbol: str, side: str,
                         max_quantity: int, max_notional: float, max_risk: float,
                         broker: str, account_id: str, environment: str,
                         ttl_seconds: int = 300, policy_version: str = POLICY,
                         now: datetime | None = None) -> dict:
    """Build a signed authorization from a deterministic ``proceed`` decision."""
    if not isinstance(decision, dict):
        raise AuthorizationError("decision must be an object")
    if decision.get("verdict") != "proceed":
        raise AuthorizationError("only a proceed verdict can be authorized")
    if environment != "paper":
        raise AuthorizationError("only paper environment is authorized")
    if side not in ("buy", "sell"):
        raise AuthorizationError("unsupported order side")
    if max_quantity <= 0 or max_notional <= 0 or max_risk <= 0:
        raise AuthorizationError("authorization limits must be positive")
    issued = now or datetime.now(timezone.utc)
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=timezone.utc)
    issued = issued.astimezone(timezone.utc)
    normalized = symbol.strip().upper().replace(".", "-")
    digest_input = "|".join([
        str(decision.get("run_id", "")), str(decision.get("generated_at", "")),
        strategy_id, normalized, side, broker, account_id])
    auth_id = "auth-" + hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:24]
    record = {
        "authorization_id": auth_id,
        "decision_id": hashlib.sha256(
            json.dumps(decision, sort_keys=True, default=str).encode()).hexdigest()[:24],
        "gate_run_id": str(decision.get("run_id", "")),
        "strategy_id": strategy_id,
        "symbol": normalized,
        "instrument": f"{broker}:{normalized}",
        "position_effect": "open",
        "side": side,
        "max_quantity": int(max_quantity),
        "max_notional": float(max_notional),
        "max_risk": float(max_risk),
        "broker": broker,
        "account_id": account_id,
        "environment": environment,
        "artifact_hashes": artifact_hashes(artifacts, root=root),
        "evidence_paths": [
            str(p.relative_to(root)).replace("\\", "/") if p.is_absolute()
            else str(p).replace("\\", "/") for p in artifacts],
        "gate_verdict": decision["verdict"],
        "gate_generated_at": str(decision.get("generated_at", "")),
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(seconds=ttl_seconds)).isoformat(),
        "state": "PENDING",
        "policy_version": policy_version,
        "idempotency_key": auth_id,
    }
    return _sign(record)


def valid_transition(old: str, new: str) -> bool:
    if old == new:
        return False
    return new in TRANSITIONS.get(old, set())


def _db_path(root: Path) -> Path:
    p = Path(root) / "outputs" / "paper" / DB_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def store_authorization(record: dict, root: Path) -> dict:
    """Persist a signed authorization, advancing PENDING -> AUTHORIZED."""
    if not verify_integrity(record):
        raise AuthorizationError("refusing to store unverified authorization")
    state = record.get("state")
    if state == "PENDING":
        if not valid_transition("PENDING", "VALIDATED"):
            raise AuthorizationError("invalid state transition")
        record = _sign({**record, "state": "VALIDATED"})
        record = _sign({**record, "state": "AUTHORIZED"})
    conn = sqlite3.connect(str(_db_path(root)), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS authorizations ("
            "id TEXT PRIMARY KEY, state TEXT NOT NULL, body TEXT NOT NULL,"
            " issued_at TEXT NOT NULL, expires_at TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO authorizations(id,state,body,issued_at,expires_at)"
            " VALUES(?,?,?,?,?)",
            (record["authorization_id"], record["state"],
             json.dumps(record, sort_keys=True), record["issued_at"],
             record["expires_at"]))
        conn.commit()
    finally:
        conn.close()
    return record


def load_authorization(auth_id: str, root: Path) -> dict | None:
    conn = sqlite3.connect(str(_db_path(root)), timeout=10)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS authorizations ("
            "id TEXT PRIMARY KEY, state TEXT NOT NULL, body TEXT NOT NULL,"
            " issued_at TEXT NOT NULL, expires_at TEXT NOT NULL)")
        row = conn.execute("SELECT body FROM authorizations WHERE id=?", (auth_id,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def validate_authorization(record: Any, *, root: Path, decision: dict,
                           artifacts: list[Path], strategy_id: str, symbol: str,
                           side: str, quantity: float, notional: float, risk: float,
                           broker: str, account_id: str, environment: str,
                           now: datetime | None = None) -> tuple[bool, str]:
    """Revalidate every binding. Returns (ok, reason); never fails open."""
    if not verify_integrity(record):
        return False, "authorization integrity check failed"
    if not isinstance(decision, dict) or decision.get("verdict") != "proceed":
        return False, "gate verdict is not proceed"
    if environment != "paper" or record.get("environment") != "paper":
        return False, "environment mismatch"
    if record.get("state") in TERMINAL:
        return False, f"authorization state {record['state']} is terminal"
    if record.get("state") not in ("PENDING", "VALIDATED", "AUTHORIZED"):
        return False, f"invalid authorization state {record.get('state')}"
    # Prefer the artifact set recorded at issue time. Recomputing it from a
    # caller-supplied decision (e.g. latest.json) would omit report evidence
    # and cause a false hash mismatch.
    bound = record.get("evidence_paths")
    evidence = [Path(p) for p in bound] if isinstance(bound, list) and bound else artifacts
    try:
        expected_hashes = artifact_hashes(evidence, root=root)
    except AuthorizationError as exc:
        return False, f"evidence unavailable: {exc}"
    if record.get("artifact_hashes") != expected_hashes:
        return False, "artifact hash mismatch"
    for field, expected in (("strategy_id", strategy_id), ("side", side),
                            ("broker", broker), ("account_id", account_id),
                            ("gate_verdict", decision["verdict"])):
        if record.get(field) != expected:
            return False, f"{field} mismatch"
    if record.get("symbol") != symbol.strip().upper().replace(".", "-"):
        return False, "symbol mismatch"
    if record.get("gate_run_id") != str(decision.get("run_id", "")):
        return False, "decision id mismatch"
    if quantity <= 0 or quantity > record.get("max_quantity", 0):
        return False, "quantity exceeds authorization"
    if notional > record.get("max_notional", 0):
        return False, "notional exceeds authorization"
    if risk > record.get("max_risk", 0):
        return False, "risk exceeds authorization"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    try:
        expires = _parse_stamp(record["expires_at"])
        issued = _parse_stamp(record["issued_at"])
    except (KeyError, ValueError, TypeError):
        return False, "invalid authorization timestamps"
    if current > expires:
        return False, "authorization expired"
    if current < issued - timedelta(seconds=60):
        return False, "authorization issued in the future"
    return True, "ok"


def reserve_authorization(auth_id: str, root: Path,
                          *, now: datetime | None = None) -> bool:
    """Atomically consume an AUTHORIZED authorization. Single winner only."""
    conn = sqlite3.connect(str(_db_path(root)), timeout=10, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT state, body FROM authorizations WHERE id=?", (auth_id,)).fetchone()
        if not row or row[0] != "AUTHORIZED":
            conn.rollback()
            return False
        body = json.loads(row[1])
        if not verify_integrity(body):
            conn.rollback()
            return False
        check_at = now or datetime.now(timezone.utc)
        if check_at.tzinfo is None:
            check_at = check_at.replace(tzinfo=timezone.utc)
        try:
            if check_at.astimezone(timezone.utc) > _parse_stamp(body["expires_at"]):
                conn.rollback()
                return False
        except (KeyError, ValueError, TypeError):
            conn.rollback()
            return False
        updated = _sign({**body, "state": "CONSUMED"})
        cursor = conn.execute(
            "UPDATE authorizations SET state='CONSUMED', body=? WHERE id=? AND state='AUTHORIZED'",
            (json.dumps(updated, sort_keys=True), auth_id))
        conn.commit()
        return cursor.rowcount == 1
    except Exception:  # noqa: BLE001 - any failure leaves the row reserved or untouched
        conn.rollback()
        return False
    finally:
        conn.close()


def gateway(*, authorization_id: str, root: Path, decision: dict,
            artifacts: list[Path], strategy_id: str, symbol: str, side: str,
            quantity: float, notional: float, risk: float, broker: str,
            account_id: str, environment: str,
            now: datetime | None = None) -> tuple[bool, str]:
    """Validate then atomically consume. This is the single execution boundary."""
    record = load_authorization(authorization_id, root)
    if record is None:
        return False, "authorization not found"
    ok, reason = validate_authorization(
        record, root=root, decision=decision, artifacts=artifacts,
        strategy_id=strategy_id, symbol=symbol, side=side, quantity=quantity,
        notional=notional, risk=risk, broker=broker, account_id=account_id,
        environment=environment, now=now)
    if not ok:
        return False, reason
    if not reserve_authorization(authorization_id, root, now=now):
        return False, "authorization already consumed or concurrent reservation won"
    return True, "authorized"
