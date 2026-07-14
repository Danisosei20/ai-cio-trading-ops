from __future__ import annotations

import tempfile
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from robinhood_tools.daily_controls import (
    BrokerOrderState,
    BrokerPosition,
    BrokerStateSnapshot,
    DailyNotice,
    FreshnessManifest,
    SourceFreshness,
    check_daily_run_watchdog,
    detect_broker_state_drift,
    render_daily_notice,
    require_no_broker_state_drift,
)
from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.health import SlackHealthWebApiNotifier
from robinhood_tools.models import EquityOrderRequest, OrderReview
from robinhood_tools.operations import build_recovery_plan
from robinhood_tools.orchestrator import DailyOrchestrator, ScreenedIdea
from robinhood_tools.risk import PortfolioState, RiskLimits
from robinhood_tools.runtime import RuntimeSettings


def settings(root: Path) -> RuntimeSettings:
    return RuntimeSettings("research_only", False, "C1", 120, root / "cio.db", root / "dashboard.html", RiskLimits())


class DailyImprovementTests(unittest.TestCase):
    def test_freshness_manifest_names_missing_and_stale_sources(self):
        now = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
        manifest = FreshnessManifest((
            SourceFreshness("quote", (now - timedelta(minutes=2)).isoformat(), timedelta(minutes=5)),
            SourceFreshness("positions", (now - timedelta(minutes=10)).isoformat(), timedelta(minutes=5)),
            SourceFreshness("earnings", None, timedelta(hours=24)),
        ))
        with self.assertRaisesRegex(PolicyViolation, "positions=stale, earnings=missing"):
            manifest.require_complete(now=now)

    def test_watchdog_detects_missing_run_and_accepts_completed_run(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            scheduled = datetime(2026, 7, 13, 9, 45, tzinfo=timezone.utc)
            missed = check_daily_run_watchdog(
                db, account_label="Agentic", scheduled_for=scheduled,
                now=scheduled + timedelta(minutes=16), grace=timedelta(minutes=15),
            )
            self.assertTrue(missed.alert_required)
            self.assertTrue(db.claim_daily_run(missed.run_key))
            db.complete_daily_run(missed.run_key, "completed", "ok")
            healthy = check_daily_run_watchdog(
                db, account_label="Agentic", scheduled_for=scheduled,
                now=scheduled + timedelta(minutes=16), grace=timedelta(minutes=15),
            )
            self.assertEqual(healthy.state, "healthy")

    def test_watchdog_health_alert_is_fixed_route_and_deduplicated(self):
        calls = []
        notifier = SlackHealthWebApiNotifier(
            bot_token="xoxb-test", channel_id="C_HEALTH",
            transport=lambda payload, token: calls.append((payload, token)) or {"ok": True},
        )
        notifier.send_health_alert(message="missed")
        self.assertEqual(calls, [({"channel": "C_HEALTH", "text": "missed"}, "xoxb-test")])
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            self.assertTrue(db.claim_health_alert("missed:1"))
            db.complete_health_alert("missed:1", sent=True)
            self.assertFalse(db.claim_health_alert("missed:1"))

    def test_standalone_watchdog_reads_completion_memory_without_desktop_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "memory.md"
            state = root / "state.json"
            memory.write_text("# Memory\n\nLast run: 2026-07-13 09:55:00 EDT\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable, "scripts/standalone_watchdog.py",
                    "--memory", str(memory), "--state-file", str(state),
                    "--health-channel", "C_HEALTH", "--now", "2026-07-13T10:05:00-04:00", "--dry-run",
                ],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"state": "healthy"', result.stdout)

    def test_standalone_watchdog_test_alert_is_unambiguous_and_does_not_write_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            result = subprocess.run(
                [
                    sys.executable, "scripts/standalone_watchdog.py",
                    "--memory", str(root / "missing-memory.md"), "--state-file", str(state),
                    "--health-channel", "C_HEALTH", "--test-alert", "--dry-run",
                ],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"state": "test_alert_ready"', result.stdout)
            self.assertIn("TEST ONLY", result.stdout)
            self.assertIn("no trade was recommended", result.stdout)
            self.assertFalse(state.exists())

    def test_shadow_equity_records_blocked_candidate_and_learning_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = CioDatabase(root / "cio.db")
            idea = ScreenedIdea(
                "AAPL", 95, True, "high quality", execution_eligible=False,
                blocked_reason="one-position cap is occupied", market_regime="risk_on",
            )
            result = DailyOrchestrator(db, settings(root)).run_daily(
                "Agentic", lambda: [idea], day=date(2026, 7, 13),
                learning_due_dates=lambda day: {1: "2026-07-14", 5: "2026-07-20", 20: "2026-08-10"},
            )
            self.assertEqual((result.action, result.shadow_symbol), ("no_action", "AAPL"))
            self.assertIn("one-position cap", result.detail)
            shadow = db.shadow_recommendations()
            self.assertEqual((shadow[0]["symbol"], shadow[0]["action"]), ("AAPL", "paper_candidate"))
            with db.connect() as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM learning_checkpoints").fetchone()[0], 3)

    def test_stale_daily_run_resumes_from_completed_screen_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = CioDatabase(root / "cio.db")
            run_key = "2026-07-13:Agentic:daily-review"
            self.assertTrue(db.claim_daily_run(run_key))
            db.save_run_checkpoint(run_key, "screen", "completed", {"ideas": [{
                "symbol": "AAPL", "score": 95, "qualified": True, "summary": "saved",
                "execution_eligible": False, "blocked_reason": "position cap", "market_regime": "risk_on",
            }]})
            with db.connect() as connection:
                connection.execute(
                    "UPDATE daily_runs SET started_at=? WHERE run_key=?",
                    ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), run_key),
                )
            result = DailyOrchestrator(db, settings(root)).run_daily(
                "Agentic", lambda: (_ for _ in ()).throw(AssertionError("screen should not rerun")),
                day=date(2026, 7, 13),
            )
            self.assertEqual(result.shadow_symbol, "AAPL")

    def test_broker_drift_blocks_recommendations_until_reconciled(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            db.begin_trade_lifecycle("AAPL", buy_approval_id="a")
            db.mark_position_open("AAPL", order_id="known-order")
            state = BrokerStateSnapshot(
                datetime.now(timezone.utc).isoformat(),
                positions=(BrokerPosition("AAPL", Decimal("1")),),
                open_orders=(BrokerOrderState("unknown-order", "MSFT", "queued"),),
            )
            issues = detect_broker_state_drift(db, state)
            self.assertEqual(issues[0].kind, "unlinked_order")
            with self.assertRaisesRegex(PolicyViolation, "reconciliation"):
                require_no_broker_state_drift(db, state)

    def test_cash_floor_uses_settled_cash_and_pending_commitments(self):
        portfolio = PortfolioState(
            equity=Decimal("100"), cash=Decimal("100"), position_weights={}, sector_weights={},
            pending_approvals=0, approved_capital_today=Decimal("0"), settled_cash=Decimal("90"),
            unsettled_cash=Decimal("10"), pending_order_commitments=Decimal("25"),
        )
        with self.assertRaisesRegex(PolicyViolation, "cash-dollar reserve"):
            RiskLimits(
                min_cash_dollars=Decimal("50"), max_position_weight=Decimal("1"),
                max_sector_weight=Decimal("1"),
            ).validate_purchase(
                portfolio, symbol="AAPL", sector="Tech", order_value=Decimal("16"),
                avg_daily_dollar_volume=Decimal("1000000"),
            )

    def test_recovery_plan_lists_stale_runs_slack_and_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            run_key = "2026-07-13:Agentic:daily-review"
            db.claim_daily_run(run_key)
            with db.connect() as connection:
                connection.execute(
                    "UPDATE daily_runs SET started_at=? WHERE run_key=?",
                    ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), run_key),
                )
            request = EquityOrderRequest("a", "AAPL", "buy", "market", "gfd", notional=Decimal("10"))
            approval = db.create(request, OrderReview("r", "a", Decimal("10")), window_minutes=10)
            db.open_reply_window(approval.approval_id, "C1", "1.1")
            db.approve(approval.approval_id)
            db.reserve_execution(approval.approval_id, request, "r")
            db.mark_reconciliation_required(approval.approval_id, "timeout")
            plan = build_recovery_plan(db)
            self.assertEqual(plan.stale_daily_run_keys, (run_key,))
            self.assertEqual(plan.open_slack_approval_ids, (approval.approval_id,))
            self.assertEqual(plan.reconciliation_approval_ids, (approval.approval_id,))

    def test_action_first_notice_is_unambiguous(self):
        message = render_daily_notice(DailyNotice(
            action="NO TRADE TODAY — HOLD VOO",
            what_to_do="Do not buy or sell anything.",
            why="The one-position cap is occupied.",
            next_review="2026-07-14 09:45 ET",
            live_trading_enabled=False,
            changes=("No material changes since the previous review.",),
            data_as_of={"quote": "2026-07-13T14:00:00+00:00"},
            watchlist=("AAPL 95/100 — monitoring only",),
        ))
        self.assertTrue(message.startswith("*ACTION: NO TRADE TODAY — HOLD VOO*"))
        self.assertIn("WATCHLIST ONLY — NOT A BUY RECOMMENDATION", message)
        self.assertIn("None — no order exists", message)
        self.assertIn("Slack cannot approve execution", message)


if __name__ == "__main__":
    unittest.main()
