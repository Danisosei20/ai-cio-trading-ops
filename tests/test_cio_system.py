from __future__ import annotations

import tempfile
import threading
import unittest
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

from robinhood_tools.analysis import ExitPlan, MarketSnapshot, ResearchSource, TradeCandidate
from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.models import Account, EquityOrderRequest, Order, OrderReview
from robinhood_tools.learning import OutcomeObservation, validate_policy_change
from robinhood_tools.risk import PortfolioState, RiskLimits
from robinhood_tools.service import RobinhoodTradingService
from robinhood_tools.universe import Sp500Snapshot
from robinhood_tools.workflow import CioWorkflow


class Backend:
    def __init__(self):
        self.placed = 0

    def list_accounts(self):
        return [Account(id="agentic", label="Agentic", agentic_allowed=True)]

    def review_equity_order(self, request):
        return OrderReview("review-1", request.account_id, request.notional, raw={"alerts": []})

    def place_equity_order(self, request, review_id):
        self.placed += 1
        return Order("order-1", request.account_id, request.symbol, request.side, "queued")

    def get_equity_order(self, order_id):
        return Order(order_id, "agentic", "AAPL", "buy", "filled")

    def cancel_equity_order(self, order_id):
        raise NotImplementedError

    def review_option_order(self, request):
        raise NotImplementedError


class UncertainBackend(Backend):
    def place_equity_order(self, request, review_id):
        raise TimeoutError("connection dropped after possible broker acceptance")


class Notifier:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages = []

    def send_approval(self, *, channel_id, message):
        if self.fail:
            raise RuntimeError("Slack unavailable")
        self.messages.append((channel_id, message))
        return {"message_ts": "1.2", "message_link": "https://slack.test/message"}


class Journal:
    def __init__(self):
        self.rows = []

    def append(self, event):
        self.rows.append(event)


def candidate(signal="supportive"):
    snapshot = MarketSnapshot(
        symbol="AAPL", observed_at=datetime.now(timezone.utc).isoformat(), quote_source="broker",
        price=Decimal("200"), bid=Decimal("199.99"), ask=Decimal("200.01"),
        session_volume=10_000_000, avg_volume_20d=9_000_000, avg_volume_50d=8_000_000,
        avg_daily_dollar_volume=Decimal("1800000000"), relative_volume=Decimal("1.1"),
        return_20d=Decimal("0.02"), return_60d=Decimal("0.08"),
        relative_strength_sp500=Decimal("0.03"), realized_volatility_20d=Decimal("0.20"),
        atr_14d=Decimal("4"), max_drawdown=Decimal("-0.08"), next_earnings_date="2026-08-01",
        signal=signal,
        sources=(
            ResearchSource("https://sec.gov/filing", "Filing", "primary", datetime.now(timezone.utc).isoformat()),
            ResearchSource("https://news.example/aapl", "News", "independent", datetime.now(timezone.utc).isoformat()),
        ),
    )
    return TradeCandidate(
        snapshot=snapshot, thesis="Durable cash flows", counter_argument="Valuation risk",
        score=92, reward_risk=Decimal("2.2"), intended_price=Decimal("200"),
        invalidation_level=Decimal("180"), target_or_review_condition="$240 or thesis change",
        expected_portfolio_weight=Decimal("0.05"), max_slippage_pct=Decimal("0.001"),
    )


class CioSystemTests(unittest.TestCase):
    def test_policy_learning_requires_ten_comparable_observations(self):
        observation = OutcomeObservation("r1", "AAPL", 20, Decimal("0.01"), Decimal("0.02"), False, Decimal("0.001"), "timing")
        with self.assertRaisesRegex(PolicyViolation, "at least 10"):
            validate_policy_change([observation] * 9, repeated_error="timing")
        result = validate_policy_change([observation] * 10, repeated_error="timing")
        self.assertEqual(result["observations"], 10)

    def test_dashboard_generation_is_read_only(self):
        output = Path(self.temp.name) / "dashboard.html"
        result = subprocess.run(
            [sys.executable, "scripts/dashboard.py", "--database", str(self.db.path), "--output", str(output)],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("AI CIO Dashboard", output.read_text())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = CioDatabase(Path(self.temp.name) / "cio.db")
        self.backend = Backend()
        universe = Sp500Snapshot(frozenset({"AAPL"}), datetime.now(timezone.utc).isoformat(), "https://spglobal.com/sp500")
        self.service = RobinhoodTradingService(self.backend, approval_store=self.db, sp500_snapshot=universe)
        self.notifier = Notifier()
        self.journal = Journal()
        self.workflow = CioWorkflow(
            self.service, self.db, self.notifier, self.journal, RiskLimits(), channel_id="C123"
        )
        self.request = EquityOrderRequest("agentic", "AAPL", "buy", "limit", "gfd", notional=Decimal("1000"), limit_price=Decimal("200"))
        self.portfolio = PortfolioState(
            equity=Decimal("100000"), cash=Decimal("20000"), position_weights={}, sector_weights={},
            pending_approvals=0, approved_capital_today=Decimal("0"), buying_power=Decimal("12000"),
        )
        self.exit_plan = ExitPlan(
            "AAPL", "Durable cash flows", datetime.now(timezone.utc).isoformat(), Decimal("190"), Decimal("240"),
            "Thesis breaks", "$240 or thesis change", Decimal("0.10"), "1-3 years", "Review before earnings", "Review taxes",
        )

    def test_end_to_end_prepare_approve_execute_and_schedule_learning(self):
        approval, review = self.workflow.prepare_purchase(
            request=self.request, candidate=candidate(), portfolio=self.portfolio,
            sector="Technology", exit_plan=self.exit_plan,
        )
        self.assertEqual(len(self.notifier.messages), 1)
        self.assertIn("Relative volume", self.notifier.messages[0][1])
        self.assertIn("**Risk controls**", self.notifier.messages[0][1])
        self.assertIn("A Slack reply does not authorize execution", self.notifier.messages[0][1])
        self.assertIn("Buying power: **$12000**", self.notifier.messages[0][1])
        self.assertIn("dollar amount or share quantity", self.notifier.messages[0][1])
        self.db.approve(approval.approval_id)
        order = self.workflow.execute_approved(
            self.request, approval_id=approval.approval_id, review_id=review.review_id, confirmed=True
        )
        self.assertEqual(order.id, "order-1")
        self.assertEqual(self.db.get(approval.approval_id).status, "executed")
        self.assertIn("Order submitted — not yet filled", self.notifier.messages[-1][1])
        with self.db.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM learning_checkpoints").fetchone()[0], 3)

    def test_no_can_reject_pending_approval_without_broker_placement(self):
        approval, _ = self.workflow.prepare_purchase(
            request=self.request, candidate=candidate(), portfolio=self.portfolio,
            sector="Technology", exit_plan=self.exit_plan,
        )
        rejected = self.db.reject(approval.approval_id)
        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(self.backend.placed, 0)

    def test_no_reply_for_ten_minutes_rejects_and_cleans_window(self):
        approval, _ = self.workflow.prepare_purchase(
            request=self.request, candidate=candidate(), portfolio=self.portfolio,
            sector="Technology", exit_plan=self.exit_plan,
        )
        rejected = self.db.reject_expired_reply_windows(datetime.now(timezone.utc) + timedelta(minutes=11))
        self.assertEqual(rejected, [approval.approval_id])
        self.assertEqual(self.db.get(approval.approval_id).status, "rejected")
        self.assertEqual(self.backend.placed, 0)
        self.assertEqual(self.db.cleanup_terminal_reply_windows(), 1)
        with self.db.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM slack_reply_windows").fetchone()[0], 0)

    def test_slack_failure_stops_before_execution(self):
        self.workflow.notifier = Notifier(fail=True)
        with self.assertRaisesRegex(PolicyViolation, "Slack delivery failed"):
            self.workflow.prepare_purchase(
                request=self.request, candidate=candidate(), portfolio=self.portfolio,
                sector="Technology", exit_plan=self.exit_plan,
            )
        self.assertEqual(self.backend.placed, 0)

    def test_insufficient_buying_power_sends_slack_notice_without_approval(self):
        portfolio = PortfolioState(
            equity=Decimal("100000"), cash=Decimal("500"), position_weights={}, sector_weights={},
            pending_approvals=0, approved_capital_today=Decimal("0"), buying_power=Decimal("500"),
        )
        with self.assertRaisesRegex(PolicyViolation, "Insufficient buying power"):
            self.workflow.prepare_purchase(
                request=self.request, candidate=candidate(), portfolio=portfolio,
                sector="Technology", exit_plan=self.exit_plan,
            )
        self.assertEqual(len(self.notifier.messages), 1)
        message = self.notifier.messages[0][1]
        self.assertIn("NO APPROVAL CREATED", message)
        self.assertIn("Shortfall: **$500**", message)
        self.assertIn("share quantity", message)
        self.assertEqual(self.db.list_approvals(), [])

    def test_unavailable_signal_fails_before_broker_review(self):
        with self.assertRaisesRegex(PolicyViolation, "unavailable"):
            self.workflow.prepare_purchase(
                request=self.request, candidate=candidate("unavailable"), portfolio=self.portfolio,
                sector="Technology", exit_plan=self.exit_plan,
            )

    def test_concurrent_execution_reservation_has_one_winner(self):
        approval, review = self.workflow.prepare_purchase(
            request=self.request, candidate=candidate(), portfolio=self.portfolio,
            sector="Technology", exit_plan=self.exit_plan,
        )
        self.db.approve(approval.approval_id)
        results = []
        barrier = threading.Barrier(2)

        def reserve():
            barrier.wait()
            try:
                self.db.reserve_execution(approval.approval_id, self.request, review.review_id)
                results.append("ok")
            except PolicyViolation:
                results.append("blocked")

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ["blocked", "ok"])

    def test_uncertain_broker_failure_requires_reconciliation(self):
        backend = UncertainBackend()
        service = RobinhoodTradingService(
            backend, approval_store=self.db, sp500_snapshot=self.service.sp500_snapshot
        )
        workflow = CioWorkflow(service, self.db, self.notifier, self.journal, RiskLimits(), channel_id="C123")
        approval, review = workflow.prepare_purchase(
            request=self.request, candidate=candidate(), portfolio=self.portfolio,
            sector="Technology", exit_plan=self.exit_plan,
        )
        self.db.approve(approval.approval_id)
        with self.assertRaises(TimeoutError):
            workflow.execute_approved(
                self.request, approval_id=approval.approval_id, review_id=review.review_id, confirmed=True
            )
        self.assertEqual(self.db.get(approval.approval_id).status, "reconciliation_required")


if __name__ == "__main__":
    unittest.main()
