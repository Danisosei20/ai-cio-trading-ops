#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from robinhood_tools.database import DATABASE_SCHEMA_VERSION


REQUIRED_TABLES = {
    "approvals", "audit_events", "decision_records", "order_fills", "schema_version",
    "trade_lifecycles", "system_controls",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_restore_drill(source: Path, *, expected_schema: int = DATABASE_SCHEMA_VERSION) -> dict:
    """Restore through SQLite's backup API into an isolated temporary database."""
    if not source.is_file():
        raise FileNotFoundError(f"Database backup not found: {source}")
    source_digest_before = _sha256(source)
    with tempfile.TemporaryDirectory(prefix="cio-restore-drill-") as directory:
        restored = Path(directory) / "restored.db"
        source_uri = source.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(source_uri, uri=True) as source_db, sqlite3.connect(str(restored)) as restored_db:
            source_db.backup(restored_db)
        with sqlite3.connect(str(restored)) as db:
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            version_row = db.execute("SELECT version FROM schema_version").fetchone()
            version = int(version_row[0]) if version_row else 0
            tables = {str(row[0]) for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        missing = sorted(REQUIRED_TABLES - tables)
        restored_digest = _sha256(restored)
    source_digest_after = _sha256(source)
    passed = (
        integrity == "ok" and version == expected_schema and not missing
        and source_digest_before == source_digest_after
    )
    return {
        "passed": passed,
        "source_unchanged": source_digest_before == source_digest_after,
        "source_sha256": source_digest_after,
        "restored_sha256": restored_digest,
        "integrity": integrity,
        "schema_version": version,
        "expected_schema_version": expected_schema,
        "missing_required_tables": missing,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a non-destructive AI CIO SQLite restore drill.")
    parser.add_argument("database", type=Path, help="Database or decrypted database backup to verify")
    parser.add_argument("--expected-schema", type=int, default=DATABASE_SCHEMA_VERSION)
    args = parser.parse_args(argv)
    try:
        result = run_restore_drill(args.database, expected_schema=args.expected_schema)
    except (FileNotFoundError, sqlite3.DatabaseError) as exc:
        result = {"passed": False, "error": type(exc).__name__, "detail": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
