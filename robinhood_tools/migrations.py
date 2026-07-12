from __future__ import annotations

from pathlib import Path

from .database import CioDatabase


CURRENT_SCHEMA_VERSION = 2


def migrate_database(path: str | Path, *, backup_path: str | Path | None = None) -> dict:
    database = CioDatabase(path)
    backup = database.backup(backup_path) if backup_path else None
    with database.connect() as connection:
        version = int(connection.execute("SELECT version FROM schema_version").fetchone()[0])
        if version < CURRENT_SCHEMA_VERSION:
            connection.execute("UPDATE schema_version SET version=?", (CURRENT_SCHEMA_VERSION,))
            version = CURRENT_SCHEMA_VERSION
    return {"schema_version": version, "integrity": database.integrity_check(),
            "backup": str(backup) if backup else None}
