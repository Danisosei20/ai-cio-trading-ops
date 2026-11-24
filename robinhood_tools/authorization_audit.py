#!/usr/bin/env python3
"""Tamper-evident, append-only audit events for authorization operations.

Each event chains to the previous hash. Raw key/signature material is dropped
before serialization and account identifiers are masked.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_AUDIT = "outputs/paper/authorization_audit.jsonl"
FORBIDDEN = {"key", "secret", "material", "signature", "authorization_key",
             "hmac_key", "raw_key"}


def _mask(value: Any) -> str:
    text = str(value)
    if len(text) <= 6:
        return "***"
    return f"{text[:2]}***{text[-3:]}"


def default_path() -> Path:
    return Path(__file__).resolve().parents[1] / DEFAULT_AUDIT


def _audit_mac() -> bytes:
    """Key the audit chain from the active signing key when available."""
    try:
        from . import authorization_keys as keys
        signing = keys.get_store().signing_key()
        if signing:
            return signing[1] + b"audit-v3"
    except Exception:  # noqa: BLE001
        pass
    return b"audit-v3-unkeyed"


def _event_hash(event: dict, prev_hash: str) -> str:
    body = {k: v for k, v in event.items() if k != "hash"}
    payload = {"prev": prev_hash, **body}
    mac = b"audit-v3-unkeyed"
    kid = body.get("kid")
    if kid:
        try:
            from . import authorization_keys as keys
            key = keys.get_store().verification_key(str(kid))
            if key:
                mac = key + b"audit-v3"
        except Exception:  # noqa: BLE001
            pass
    return hmac.new(mac, json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str).encode(), hashlib.sha256).hexdigest()


def _candidate_macs() -> list[bytes]:
    macs = [_audit_mac()]
    try:
        from . import authorization_keys as keys
        for row in keys.get_store().status():
            if row["status"] in {"ACTIVE", "VERIFY_ONLY"}:
                key = keys.get_store().verification_key(row["kid"])
                if key:
                    macs.append(key + b"audit-v3")
    except Exception:  # noqa: BLE001
        pass
    return list(dict.fromkeys(macs))


class _AuditLock:
    """Cross-process advisory lock via a dedicated lock file."""

    def __init__(self, target: Path):
        self.lock_path = target.with_suffix(".lock")
        self.fh = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.lock_path, "a+", encoding="utf-8")
        try:
            if sys.platform == "win32":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        except Exception:  # noqa: BLE001 - advisory only
            pass
        return self

    def __exit__(self, *args):
        try:
            if sys.platform == "win32":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001
            pass
        self.fh.close()
        return False


def emit(event: str, *, path: Path | None = None, **fields: Any) -> dict:
    target = Path(path) if path is not None else default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    clean = {}
    for key, value in fields.items():
        lowered = str(key).lower()
        if lowered in FORBIDDEN or "secret" in lowered or "password" in lowered:
            continue
        clean[key] = _mask(value) if lowered in {"account_id", "broker_account", "account"} else value
    with _AuditLock(target):
        prev = ""
        if target.exists():
            lines = [ln for ln in target.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                try:
                    prev = str(json.loads(lines[-1]).get("hash", ""))
                except (ValueError, TypeError):
                    prev = ""
        try:
            from . import authorization_keys as keys
            signing = keys.get_store().signing_key()
            active_kid = signing[0] if signing else None
        except Exception:  # noqa: BLE001
            active_kid = None
        row: dict[str, Any] = {
            "kid": active_kid,
            "event_id": "evt-" + uuid.uuid4().hex[:16],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event, **clean, "prev_hash": prev,
        }
        row["hash"] = _event_hash(row, prev)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:  # noqa: BLE001 - advisory on platforms without fsync
                pass
    return row


def verify_chain(path: Path | None = None) -> bool:
    target = Path(path) if path is not None else default_path()
    if not target.exists():
        return False
    prev = ""
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("prev_hash") != prev:
                return False
            if row.get("hash") not in {
                hmac.new(mac, json.dumps(
                    {"prev": prev, **{k: v for k, v in row.items() if k != "hash"}},
                    sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False, default=str).encode(), hashlib.sha256).hexdigest()
                for mac in _candidate_macs()
            }:
                return False
            prev = str(row.get("hash", ""))
    except (OSError, ValueError, TypeError):
        return False
    return True
