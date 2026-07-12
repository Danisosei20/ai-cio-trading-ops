from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.migrations import CURRENT_SCHEMA_VERSION, migrate_database
from robinhood_tools.models import EquityOrderRequest, Order, OrderReview
from robinhood_tools.operations import (
    apply_losing_exit_cooldown,
    monitor_slack_replies_once,
    monitor_slack_reply_window,
    poll_until_terminal,
    report_service_failure,
    resume_open_slack_monitors,
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


class Slack:
    def __init__(self): self.acks = []
    def replies(self, **kwargs): return [{"ts": "2.1", "text": "NO"}]
    def acknowledge(self, **kwargs): self.acks.append(kwargs["message"])


class SizedSlack(Slack):
    def replies(self, **kwargs): return [{"ts": "3.1", "text": "$10"}]


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

    def test_slack_no_rejects_but_never_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            request = EquityOrderRequest("a", "AAPL", "buy", "market", "gfd", notional=Decimal("10"))
            review = OrderReview("r", "a", Decimal("10"))
            approval = db.create(request, review, window_minutes=10)
            db.open_reply_window(approval.approval_id, "C1", "1.1")
            slack = Slack()
            result = monitor_slack_replies_once(db, slack, approval_id=approval.approval_id,
                                                channel_id="C1", parent_message_ts="1.1")
            self.assertEqual(result[0]["state"], "rejected")
            self.assertEqual(db.get(approval.approval_id).status, "rejected")
            self.assertEqual(len(slack.acks), 1)

    def test_migration_is_repeatable_and_backup_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = migrate_database(root / "cio.db", backup_path=root / "backup.db")
            self.assertEqual(result["schema_version"], CURRENT_SCHEMA_VERSION)
            self.assertEqual(migrate_database(root / "cio.db")["integrity"], "ok")

    def test_losing_invalidated_exit_creates_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            self.assertTrue(apply_losing_exit_cooldown(
                db, symbol="AAPL", realized_profit=Decimal("-2"), thesis_invalidated=True,
                starts_on="2026-07-13", expires_on="2026-07-20",
            ))
            with self.assertRaises(PolicyViolation):
                db.require_no_symbol_cooldown("AAPL", today="2026-07-15")

    def test_event_monitor_routes_size_without_user_returning_to_codex(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            request = EquityOrderRequest("a", "AAPL", "buy", "market", "gfd", notional=Decimal("10"))
            approval = db.create(request, OrderReview("r", "a", Decimal("10")), window_minutes=10)
            db.open_reply_window(approval.approval_id, "C1", "1.1")
            result = monitor_slack_reply_window(
                db, SizedSlack(), approval_id=approval.approval_id, channel_id="C1",
                parent_message_ts="1.1", timeout_seconds=0, poll_interval_seconds=0.01,
            )
            self.assertEqual(result.state, "fresh_review_required")
            self.assertEqual(result.outcomes[0]["value"], "10")
            self.assertEqual(db.get(approval.approval_id).status, "pending")

    def test_restart_resumes_persisted_open_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            request = EquityOrderRequest("a", "AAPL", "buy", "market", "gfd", notional=Decimal("10"))
            approval = db.create(request, OrderReview("r", "a", Decimal("10")), window_minutes=10)
            db.open_reply_window(approval.approval_id, "C1", "1.1")
            result = resume_open_slack_monitors(db, SizedSlack(), timeout_seconds=0, poll_interval_seconds=0.01)
            self.assertEqual(result[approval.approval_id].state, "fresh_review_required")

    def test_independent_health_notifier_reports_failure_without_secrets(self):
        health = Health()
        report_service_failure(health, component="scheduler", error=RuntimeError("token=secret"))
        self.assertIn("scheduler failed", health.messages[0])
        self.assertNotIn("secret", health.messages[0])


if __name__ == "__main__":
    unittest.main()
