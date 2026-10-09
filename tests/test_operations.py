from __future__ import annotations

import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.migrations import CURRENT_SCHEMA_VERSION, migrate_database
from robinhood_tools.models import Order
from robinhood_tools.operations import (
    apply_losing_exit_cooldown,
    poll_until_terminal,
    report_service_failure,
)
from robinhood_tools.reconciliation import Fill


class Orders:
    def __init__(self): self.calls = 0
    def get_order(self, **kwargs):
        self.calls += 1
        status = "partially_filled" if self.calls == 1 else "filled"
        return Order(kwargs["order_id"], kwargs["account_id"], "AAPL", "sell", status)
    def get_fills(self, **kwargs):
        return [Fill(f"f{self.calls}", Decimal("1"), Decimal("120"), Decimal("0"), f"2026-07-13T14:0{self.calls}:00Z")]


class Health:
    def __init__(self): self.messages = []
    def send_health_alert(self, *, message): self.messages.append(message)


class OperationsTests(unittest.TestCase):
    def test_fill_poller_stops_at_terminal_and_keeps_partial_fills(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            result = poll_until_terminal(db, Orders(), account_id="a", order_id="o", attempts=3, interval_seconds=0)
            self.assertTrue(result.terminal)
            self.assertEqual(result.reconciliation.filled_quantity, Decimal("2"))

    def test_migration_is_repeatable_and_backup_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "cio.db"
            backup_path = root / "backup.db"
            with sqlite3.connect(database_path) as database:
                database.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
                database.execute("INSERT INTO schema_version VALUES (?)", (CURRENT_SCHEMA_VERSION - 1,))
                database.execute("CREATE TABLE legacy_record (value TEXT NOT NULL)")
                database.execute("INSERT INTO legacy_record VALUES ('preserved')")

            result = migrate_database(database_path, backup_path=backup_path)
            self.assertEqual(result["schema_version"], CURRENT_SCHEMA_VERSION)
            self.assertEqual(result["backup"], str(backup_path))
            with sqlite3.connect(backup_path) as backup:
                self.assertEqual(backup.execute("SELECT version FROM schema_version").fetchone()[0],
                                 CURRENT_SCHEMA_VERSION - 1)
                self.assertIsNone(backup.execute(
                    "SELECT name FROM sqlite_master WHERE name='research_experiments'"
                ).fetchone())
            with sqlite3.connect(database_path) as migrated:
                self.assertEqual(migrated.execute("SELECT version FROM schema_version").fetchone()[0],
                                 CURRENT_SCHEMA_VERSION)
                self.assertIsNotNone(migrated.execute(
                    "SELECT name FROM sqlite_master WHERE name='research_experiments'"
                ).fetchone())
            self.assertEqual(migrate_database(database_path)["integrity"], "ok")

    def test_losing_invalidated_exit_creates_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            self.assertTrue(apply_losing_exit_cooldown(
                db, symbol="AAPL", realized_profit=Decimal("-2"), thesis_invalidated=True,
                starts_on="2026-07-13", expires_on="2026-07-20",
            ))
            with self.assertRaises(PolicyViolation):
                db.require_no_symbol_cooldown("AAPL", today="2026-07-15")

    def test_independent_health_notifier_reports_failure_without_secrets(self):
        health = Health()
        report_service_failure(health, component="scheduler", error=RuntimeError("token=secret"))
        self.assertIn("scheduler failed", health.messages[0])
        self.assertNotIn("secret", health.messages[0])


if __name__ == "__main__":
    unittest.main()
