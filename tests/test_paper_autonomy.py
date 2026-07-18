from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from robinhood_tools.analysis import ExitPlan, MarketSnapshot, ResearchSource, TradeCandidate
from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.models import Account, EquityOrderRequest, Order, OrderReview
from robinhood_tools.paper_autonomy import PaperAutoExecutor, PaperEntryContext, TradingViewChartAnalysis
from robinhood_tools.risk import PortfolioState, RiskLimits
from robinhood_tools.runtime import PaperAutonomySettings, RuntimeSettings
from robinhood_tools.service import RobinhoodTradingService
from robinhood_tools.universe import Sp500Snapshot


class Backend:
    def list_accounts(self):
        return [Account("paper-account", "Alpaca Paper", True, account_type="paper")]

    def review_equity_order(self, request):
        return OrderReview("paper-review", request.account_id, request.notional, raw={"paper": True})

    def place_equity_order(self, request, review_id):
        return Order("paper-order", request.account_id, request.symbol, request.side, "queued")

    def get_equity_order(self, order_id):
        return Order(order_id, "paper-account", "AAPL", "buy", "queued")

    def cancel_equity_order(self, order_id):
        raise NotImplementedError

    def review_option_order(self, request):
        raise NotImplementedError


class Notifier:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.messages = []

    def send_approval(self, *, channel_id, message):
        if self.fail:
            raise RuntimeError("Slack down")
        self.messages.append((channel_id, message))
        return {"message_ts": "1.2", "message_link": "https://slack.test/paper"}


class Journal:
    def __init__(self):
        self.rows = []

    def append(self, event):
        self.rows.append(event)


def _candidate() -> TradeCandidate:
    now = datetime.now(timezone.utc).isoformat()
    snapshot = MarketSnapshot(
        "AAPL", now, "alpaca-iex", Decimal("200"), Decimal("199.99"), Decimal("200.01"),
        10_000_000, 9_000_000, 8_000_000, Decimal("1800000000"), Decimal("1.6"),
        Decimal("0.02"), Decimal("0.08"), Decimal("0.03"), Decimal("0.20"),
        Decimal("4"), Decimal("-0.08"), "2026-08-01", "supportive",
        (
            ResearchSource("https://sec.gov/filing", "SEC filing", "primary", now),
            ResearchSource("https://news.example/aapl", "Independent news", "independent", now),
        ),
    )
    return TradeCandidate(
        snapshot, "Durable cash flow and confirmed trend.", "Valuation could compress.", 92,
        Decimal("2.6"), Decimal("200"), Decimal("190"), "$220 or thesis review",
        Decimal("0.005"), Decimal("0.001"), market_regime="risk_on",
    )


class PaperAutonomyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = CioDatabase(Path(self.temp.name) / "paper.db")
        self.policy = PaperAutonomySettings(
            enabled=True, human_approval_required=False, earliest_entry_time_et="00:00",
            latest_entry_time_et="23:59",
        )
        self.settings = RuntimeSettings(
            "paper_auto", False, "C1", 120, self.db.path, Path(self.temp.name) / "paper.html",
            RiskLimits(
                max_order_value=Decimal("500"), max_symbol_exposure=Decimal("500"),
                min_cash_dollars=Decimal("50"), max_open_positions=1,
            ),
            paper_trading_enabled=True, paper_autonomy=self.policy,
        )
        universe = Sp500Snapshot(
            frozenset({"AAPL"}), datetime.now(timezone.utc).isoformat(), "https://spglobal.com/sp500",
        )
        self.service = RobinhoodTradingService(
            Backend(), approval_store=self.db, sp500_snapshot=universe,
            broker_environment="paper", require_human_confirmation=False,
        )
        self.notifier = Notifier()
        self.journal = Journal()
        self.executor = PaperAutoExecutor(self.service, self.db, self.notifier, self.journal, self.settings)
        self.request = EquityOrderRequest(
            "paper-account", "AAPL", "buy", "limit", "gfd",
            notional=Decimal("500"), limit_price=Decimal("200"),
        )
        self.portfolio = PortfolioState(
            Decimal("100000"), Decimal("100000"), {}, {}, 0, Decimal("0"),
            buying_power=Decimal("100000"), settled_cash=Decimal("100000"), open_positions=0,
        )
        self.context = PaperEntryContext(
            datetime.now(timezone.utc).isoformat(), True, "supportive", "Alpaca 5-minute bars",
            False, tradingview_analysis=TradingViewChartAnalysis(
                symbol="AAPL",
                exchange="NASDAQ",
                timeframe="5m",
                observed_at=datetime.now(timezone.utc).isoformat(),
                source_url="https://www.tradingview.com/chart/test/",
                signal="supportive",
                pattern="bull flag",
                notes="Price held above the opening range.",
            ),
        )
        self.exit_plan = ExitPlan(
            "AAPL", "Durable cash flow", datetime.now(timezone.utc).isoformat(),
            Decimal("190"), Decimal("220"), "Close below $190", "$220 or thesis review",
            Decimal("0.01"), "days to weeks", "Exit or reduce before earnings blackout",
            "Paper account; no tax consequence",
        )

    def test_autonomous_purchase_uses_policy_authorization_and_post_trade_slack(self):
        result = self.executor.execute_purchase(
            request=self.request, candidate=_candidate(), portfolio=self.portfolio,
            sector="Technology", exit_plan=self.exit_plan, context=self.context,
        )
        self.assertEqual(result.order.id, "paper-order")
        self.assertEqual(self.db.get(result.policy_authorization_id).status, "executed")
        self.assertEqual(result.notification_status, "sent")
        self.assertEqual(self.db.get_trade_lifecycle("AAPL")["status"], "buy_pending")
        message = self.notifier.messages[0][1]
        self.assertIn("PAPER TRADE SUBMITTED", message)
        self.assertIn("no fill above this price", message)
        self.assertIn("no live funds", message)
        self.assertIn(result.order_fingerprint, message)

    def test_slack_failure_does_not_misreport_or_retry_broker_order(self):
        self.executor.notifier = Notifier(fail=True)
        result = self.executor.execute_purchase(
            request=self.request, candidate=_candidate(), portfolio=self.portfolio,
            sector="Technology", exit_plan=self.exit_plan, context=self.context,
        )
        self.assertEqual(result.notification_status, "failed")
        self.assertEqual(self.db.get(result.policy_authorization_id).status, "executed")

    def test_price_chasing_market_orders_and_more_than_500_are_blocked(self):
        market = EquityOrderRequest(
            "paper-account", "AAPL", "buy", "market", "gfd", notional=Decimal("10"),
        )
        with self.assertRaisesRegex(PolicyViolation, "limit order"):
            self.executor.execute_purchase(
                request=market, candidate=_candidate(), portfolio=self.portfolio,
                sector="Technology", exit_plan=self.exit_plan, context=self.context,
            )
        too_large = EquityOrderRequest(
            "paper-account", "AAPL", "buy", "limit", "gfd",
            notional=Decimal("500.01"), limit_price=Decimal("200"),
        )
        with self.assertRaisesRegex(PolicyViolation, "maximum order"):
            self.executor.execute_purchase(
                request=too_large, candidate=_candidate(), portfolio=self.portfolio,
                sector="Technology", exit_plan=self.exit_plan, context=self.context,
            )

    def test_panic_selloff_requires_reclaim_stabilization_and_higher_reward_risk(self):
        context = PaperEntryContext(
            datetime.now(timezone.utc).isoformat(), True, "supportive", "Alpaca 5-minute bars",
            False, panic_selloff=True, reclaimed_vwap_or_opening_range=False, stabilization_bars=3,
        )
        with self.assertRaisesRegex(PolicyViolation, "VWAP or opening-range reclaim"):
            self.executor.execute_purchase(
                request=self.request, candidate=_candidate(), portfolio=self.portfolio,
                sector="Technology", exit_plan=self.exit_plan, context=context,
            )

    def test_live_service_cannot_disable_human_confirmation(self):
        with self.assertRaisesRegex(PolicyViolation, "only for paper"):
            RobinhoodTradingService(
                Backend(), broker_environment="live", require_human_confirmation=False,
            )

    def test_conflicting_tradingview_analysis_blocks_entry(self):
        context = PaperEntryContext(
            datetime.now(timezone.utc).isoformat(), True, "supportive", "Alpaca 5-minute bars",
            False, tradingview_analysis=TradingViewChartAnalysis(
                symbol="AAPL",
                exchange="NASDAQ",
                timeframe="5m",
                observed_at=datetime.now(timezone.utc).isoformat(),
                source_url="https://www.tradingview.com/chart/test/",
                signal="conflicting",
            ),
        )
        with self.assertRaisesRegex(PolicyViolation, "TradingView conflicts"):
            self.executor.execute_purchase(
                request=self.request, candidate=_candidate(), portfolio=self.portfolio,
                sector="Technology", exit_plan=self.exit_plan, context=context,
            )


if __name__ == "__main__":
    unittest.main()
