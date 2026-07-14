from __future__ import annotations

import sqlite3
from pathlib import Path

from .database import CioDatabase, DATABASE_SCHEMA_VERSION


CURRENT_SCHEMA_VERSION = DATABASE_SCHEMA_VERSION


def migrate_database(path: str | Path, *, backup_path: str | Path | None = None) -> dict:
    database_path = Path(path)
    backup = None
    if backup_path and database_path.exists():
        backup = Path(backup_path)
        if database_path.resolve() == backup.resolve():
            raise ValueError("Migration backup path must differ from the database path.")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)

    database = CioDatabase(database_path)
    with database.connect() as connection:
        version = int(connection.execute("SELECT version FROM schema_version").fetchone()[0])
        if version < CURRENT_SCHEMA_VERSION:
            connection.execute("UPDATE schema_version SET version=?", (CURRENT_SCHEMA_VERSION,))
            version = CURRENT_SCHEMA_VERSION
    return {"schema_version": version, "integrity": database.integrity_check(),
            "backup": str(backup) if backup else None}
