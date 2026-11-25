#!/usr/bin/env python3
"""Append-only, atomic terminal-status manifests for scheduled workflows.

A scheduler result code alone is never proof of execution. Every wrapper must
write one manifest with its own start/finish, child exit codes, evidence refs
and a terminal status from the controlled vocabulary.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERIFIED = "VERIFIED"
FAILED = "FAILED"
SKIPPED = "SKIPPED"
NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
NOT_TESTED = "NOT_TESTED"
INCOMPLETE = "INCOMPLETE"
TERMINAL = {VERIFIED, FAILED, SKIPPED, NO_ACTION_REQUIRED,
            RECONCILIATION_REQUIRED, NOT_TESTED}
TASK_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")


class ManifestError(ValueError):
    pass


def _canonical(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _manifest_key() -> bytes:
    from robinhood_tools.market_snapshot import snapshot_key  # noqa: PLC0415
    return snapshot_key()


def _digest(doc: dict) -> str:
    return hmac.new(_manifest_key(), _canonical(doc).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def manifest_path(root: Path, task: str, date: str) -> Path:
    if not TASK_RE.match(str(task)):
        raise ManifestError(f"invalid task id: {task!r}")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date)):
        raise ManifestError(f"invalid date: {date!r}")
    return Path(root) / "outputs" / "runs" / date / f"{task}.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if any(parent.is_symlink() for parent in (path.parent, *path.parents)
           if parent.parent != parent):
        raise ManifestError(f"symlink in manifest path: {path}")
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def write_manifest(root: Path, *, task: str, date: str, status: str,
                   run_id: str, started_at: str, finished_at: str,
                   exit_code: int | None, mode: str,
                   evidence: dict[str, Any] | None = None,
                   error: str = "") -> dict:
    """Write one manifest per task per session (latest terminal outcome)."""
    if status not in TERMINAL | {INCOMPLETE}:
        raise ManifestError(f"invalid status: {status!r}")
    record: dict[str, Any] = {
        "task": task, "run_id": run_id, "trading_date": date,
        "status": status, "terminal": status in TERMINAL,
        "started_at": started_at, "finished_at": finished_at,
        "exit_code": exit_code, "execution_mode": mode,
        "evidence": evidence or {}, "error": error[:500],
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    record["hash_mode"] = "hmac-sha256"
    record["sha256"] = _digest({k: v for k, v in record.items()
                                if k != "sha256"})
    path = manifest_path(root, task, date)
    _atomic_write(path, json.dumps(record, indent=2, ensure_ascii=False,
                                   default=str) + "\n")
    return {"path": str(path), "sha256": record["sha256"]}


def read_manifest(root: Path, task: str, date: str) -> dict | None:
    path = manifest_path(root, task, date)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    expected = doc.get("sha256")
    actual = _digest({k: v for k, v in doc.items() if k != "sha256"})
    if not isinstance(expected, str) or not hmac.compare_digest(expected, actual):
        return None
    return doc
