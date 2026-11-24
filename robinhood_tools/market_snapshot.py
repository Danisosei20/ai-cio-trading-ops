#!/usr/bin/env python3
"""Immutable, per-cycle market snapshots with keyed hashes and replay.

Writes only under ``outputs/market_snapshots/<date>/<cycle_id>/<symbol>/``.
Final files are created with O_CREAT|O_EXCL so duplicate cycle writes fail
atomically. A local HMAC key (env ``CIO_SNAPSHOT_HMAC_KEY`` or a private key
file) prevents an attacker holding only the artifact from recomputing its
hash. Record identity is bound to the path; replay verifies every snapshot.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CYCLE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,10}(\.[A-Z0-9]{1,3})?$")
ROOT_REL = Path("outputs/market_snapshots")
STRATEGY_VERSION = "phase8.5-snapshots-v1"
_KEY_CACHE: bytes | None = None


class SnapshotError(ValueError):
    pass


def snapshot_key() -> bytes:
    """Local HMAC key; never written into artifacts."""
    global _KEY_CACHE
    if _KEY_CACHE is not None:
        return _KEY_CACHE
    from_env = os.environ.get("CIO_SNAPSHOT_HMAC_KEY", "").encode("utf-8")
    if from_env:
        _KEY_CACHE = from_env
        return _KEY_CACHE
    path = Path.home() / ".cio" / "snapshot_hmac.key"
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(secrets.token_bytes(32))
            try:
                path.chmod(0o600)
            except OSError:
                pass
        _KEY_CACHE = path.read_bytes()
    except OSError:
        _KEY_CACHE = secrets.token_bytes(32)  # process-local fallback
    return _KEY_CACHE


def canonical_hash(doc: dict, key: bytes | None = None) -> str:
    payload = json.dumps(doc, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str).encode("utf-8")
    return hmac.new(key if key is not None else snapshot_key(), payload,
                    hashlib.sha256).hexdigest()


def snapshot_path(root: Path, date: str, cycle_id: str, symbol: str) -> Path:
    symbol = str(symbol).strip().upper()
    if not CYCLE_RE.match(str(cycle_id)):
        raise SnapshotError(f"invalid cycle id: {cycle_id!r}")
    if not SYMBOL_RE.match(symbol):
        raise SnapshotError(f"invalid symbol: {symbol!r}")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date)):
        raise SnapshotError(f"invalid date: {date!r}")
    return Path(root) / ROOT_REL / date / cycle_id / symbol / "snapshot.json"


def _safe_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in (path.parent, *path.parents):
        if parent.parent == parent:
            break
        if parent.is_symlink():
            raise SnapshotError(f"symlink in snapshot path: {parent}")
    # Exclusive create: concurrent writers cannot both succeed, and no
    # temporary file can be swapped in behind our back.
    data = text.encode("utf-8")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SnapshotError(f"duplicate snapshot: {path}") from exc
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def write_snapshot(root: Path, *, date: str, cycle_id: str, symbol: str,
                   bars: list[dict], provider: str, feed: str,
                   timeframe: str, indicator_values: dict | None = None,
                   signal_id: str | None = None,
                   freshness: str = "unknown", completeness: str = "unknown",
                   now: str | None = None) -> dict:
    """Persist one immutable snapshot; duplicate cycle/symbol writes fail."""
    path = snapshot_path(root, date, cycle_id, symbol)
    stamp = now or datetime.now(timezone.utc).isoformat()
    record: dict[str, Any] = {
        "date": date, "cycle_id": cycle_id, "symbol": str(symbol).strip().upper(),
        "provider": provider, "feed": feed, "timeframe": timeframe,
        "retrieved_at": stamp,
        "market_timestamps": [b.get("t") or b.get("timestamp") or b.get("datetime")
                              for b in bars if isinstance(b, dict)],
        "bars": bars, "data_completeness": completeness, "freshness": freshness,
        "freshness_verified": False,  # evaluated separately by signal_freshness
        "indicator_values": indicator_values or {},
        "signal_id": signal_id, "strategy_version": STRATEGY_VERSION,
        "calculated_at": stamp,
        "hash_mode": "hmac-sha256",
    }
    record["evidence_sha256"] = canonical_hash(record)
    _safe_write(path, json.dumps(record, indent=2, ensure_ascii=False,
                                 default=str) + "\n")
    return {"path": str(path), "evidence_sha256": record["evidence_sha256"]}


def verify_snapshot(path: Path) -> tuple[bool, str]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return False, f"unreadable: {type(exc).__name__}"
    stored = doc.get("evidence_sha256")
    actual = canonical_hash({k: v for k, v in doc.items()
                             if k != "evidence_sha256"})
    if stored != actual:
        return False, "evidence hash mismatch"
    try:
        expected = snapshot_path(Path.cwd(), doc.get("date", ""),
                                 doc.get("cycle_id", ""), doc.get("symbol", ""))
        suffix = expected.parts[-4:]
        actual_suffix = Path(path).parts[-4:]
        if suffix != actual_suffix:
            return False, "record/path identity mismatch"
    except SnapshotError as exc:
        return False, f"invalid record identity: {exc}"
    return True, "ok (keyed HMAC)"


def load_snapshot(path: Path) -> dict:
    ok, reason = verify_snapshot(path)
    if not ok:
        raise SnapshotError(reason)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def replay_snapshots(root: Path, date: str, cycle_id: str | None = None) -> list[dict]:
    """Deterministic replay ordered by cycle/symbol with integrity checks."""
    base = Path(root) / ROOT_REL / date
    if not base.is_dir():
        return []
    rows = []
    for path in sorted(base.glob("*/*/snapshot.json"),
                       key=lambda candidate: (candidate.parent.parent.name,
                                              candidate.parent.name)):
        if cycle_id and path.parent.parent.name != cycle_id:
            continue
        ok, reason = verify_snapshot(path)
        rows.append({"path": str(path), "integrity": ok, "reason": reason,
                     "cycle_id": path.parent.parent.name,
                     "symbol": path.parent.name})
    return rows
