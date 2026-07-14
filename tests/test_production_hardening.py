from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

from robinhood_tools.adapters import RetryingNotifier
from robinhood_tools.database import CioDatabase
from robinhood_tools.errors import PolicyViolation
from robinhood_tools.market_calendar import MarketSession, add_trading_days, require_open_session, trading_days_until
from robinhood_tools.models import Account, EquityOrderRequest
from robinhood_tools.paper import PaperTradingBackend
from robinhood_tools.portfolio import TaxLot, rank_lots_for_tax_aware_sale, wash_sale_warning
from robinhood_tools.privacy import create_safe_support_bundle
from robinhood_tools.runtime import build_settings, build_live_service
from robinhood_tools.slack_replies import parse_safe_reply, reply_acknowledgement
from robinhood_tools.universe import Sp500Snapshot


class FlakySlack:
    def __init__(self): self.calls = 0
    def send_approval(self, **kwargs):
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("timeout")
        return {"message_ts": "1.2"}


class Calendar:
    def __init__(self, is_open): self.is_open = is_open
    def session(self, day):
        return MarketSession(day, self.is_open, "16:00" if self.is_open else None, "https://nyse.com/calendar", datetime.now(timezone.utc).isoformat(), "holiday")


class ProductionHardeningTests(unittest.TestCase):
    def build_test_settings(
        self, directory, config_path="config/approval_routes.example.json", mode="paper_auto",
    ):
        env = Path(directory) / ".env.test"
        env.write_text(
            "TIMEZONE=America/New_York\n"
            "SLACK_CHANNEL_ID=C_TEST\n"
            "HEALTH_SLACK_CHANNEL_ID=C_HEALTH_TEST\n"
            "APPROVAL_WINDOW_MINUTES=120\n"
            "ROBINHOOD_ACCOUNT_NICKNAME=Test\n"
            "INVESTMENT_OBJECTIVE=Testing\n"
            "RISK_TOLERANCE=moderate\n"
            "TAX_CONTEXT=test\n"
            f"TRADING_MODE={mode}\n"
            "TRADING_ENABLED=false\n",
            encoding="utf-8",
        )
        return build_settings(config_path, env)

    def test_database_schema_config_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = json.loads(Path("config/approval_routes.example.json").read_text(encoding="utf-8"))
            config["runtime"]["database_schema_version"] -= 1
            config_path = Path(directory) / "stale-config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(PolicyViolation, "Configured database schema"):
                self.build_test_settings(directory, config_path)

    def test_broker_mode_routing_cannot_be_swapped(self):
        with tempfile.TemporaryDirectory() as directory:
            config = json.loads(Path("config/approval_routes.example.json").read_text(encoding="utf-8"))
            config["runtime"]["paper_broker"] = "robinhood"
            config_path = Path(directory) / "unsafe-routing.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(PolicyViolation, "paper_broker=alpaca"):
                self.build_test_settings(directory, config_path)

    def test_live_kill_switch_defaults_off(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.build_test_settings(directory)
            self.assertEqual(settings.mode, "paper_auto")
            self.assertFalse(settings.trading_enabled)
            with self.assertRaisesRegex(PolicyViolation, "kill switch"):
                settings.require_live_trading()

    def test_production_service_factory_enforces_kill_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.build_test_settings(directory, mode="live_approval")
            backend = PaperTradingBackend([Account("paper", "Paper", True)], {"AAPL": Decimal("200")})
            universe = Sp500Snapshot(frozenset({"AAPL"}), datetime.now(timezone.utc).isoformat(), "https://spglobal.com/sp500")
            service = build_live_service(backend, settings=settings, sp500_snapshot=universe)
            request = EquityOrderRequest("paper", "AAPL", "buy", "market", "gfd", notional=Decimal("100"))
            with self.assertRaisesRegex(PolicyViolation, "kill switch"):
                service.place_equity_order(request, review_id="x", approval_id="x", confirmed=True)

    def test_paper_backend_never_needs_connector(self):
        backend = PaperTradingBackend([Account("paper", "Paper", True)], {"AAPL": Decimal("200")})
        request = EquityOrderRequest("paper", "AAPL", "buy", "market", "gfd", notional=Decimal("1000"))
        review = backend.review_equity_order(request)
        order = backend.place_equity_order(request, review.review_id)
        self.assertTrue(order.id.startswith("paper-order-"))
        self.assertEqual(order.status, "filled")

    def test_database_backup_integrity_and_daily_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            self.assertEqual(db.integrity_check(), "ok")
            self.assertTrue(db.claim_daily_run("2026-07-13:agentic:morning"))
            self.assertFalse(db.claim_daily_run("2026-07-13:agentic:morning"))
            backup = db.backup(Path(directory) / "backup.db")
            self.assertEqual(CioDatabase(backup).integrity_check(), "ok")

    def test_retrying_notifier_retries_notifications_only(self):
        delegate = FlakySlack()
        result = RetryingNotifier(delegate).send_approval(channel_id="C1", message="test")
        self.assertEqual(result["retry_count"], 2)
        self.assertEqual(delegate.calls, 3)

    def test_market_calendar_fails_closed(self):
        with self.assertRaisesRegex(PolicyViolation, "closed"):
            require_open_session(Calendar(False), date.today())
        self.assertTrue(require_open_session(Calendar(True), date.today()).is_open)
        self.assertEqual(add_trading_days(Calendar(True), date(2026, 7, 13), 5), date(2026, 7, 18))
        self.assertEqual(trading_days_until(Calendar(True), date(2026, 7, 13), date(2026, 7, 18)), 5)

    def test_tax_lot_ranking_and_wash_sale_warning(self):
        loss = TaxLot("AAPL", date.today() - timedelta(days=500), Decimal("1"), Decimal("220"), Decimal("200"))
        gain = TaxLot("AAPL", date.today() - timedelta(days=100), Decimal("1"), Decimal("180"), Decimal("200"))
        self.assertIs(rank_lots_for_tax_aware_sale([gain, loss])[0], loss)
        self.assertTrue(wash_sale_warning([loss], [date.today()]))

    def test_support_bundle_excludes_personal_files(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            bundle = create_safe_support_bundle(root, Path(directory) / "support.zip")
            with zipfile.ZipFile(bundle) as archive:
                names = archive.namelist()
                self.assertNotIn(".env", names)
                self.assertNotIn("config/approval_routes.json", names)
                self.assertIn(".env.example", names)

    def test_slack_reply_parser_never_creates_execution_authority(self):
        amount = parse_safe_reply("TEST SIZE $50")
        shares = parse_safe_reply("TEST SHARES 0.25")
        blocked = parse_safe_reply("APPROVE real-order")
        self.assertEqual((amount.kind, amount.value), ("dollar_amount", Decimal("50")))
        self.assertEqual((shares.kind, shares.value), ("share_quantity", Decimal("0.25")))
        self.assertEqual(blocked.kind, "execution_blocked")
        self.assertIn("require Codex", reply_acknowledgement(blocked))
        self.assertEqual(parse_safe_reply("YES").kind, "yes_request_sizing")
        self.assertEqual(parse_safe_reply("NO").kind, "reject")
        self.assertEqual(parse_safe_reply("$50").value, Decimal("50"))
        self.assertEqual(parse_safe_reply("0.25 shares").value, Decimal("0.25"))
        self.assertEqual(parse_safe_reply("YES, $10").value, Decimal("10"))
        self.assertEqual(parse_safe_reply("yes 0.25 shares").value, Decimal("0.25"))

    def test_slack_reply_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            db = CioDatabase(Path(directory) / "cio.db")
            self.assertTrue(db.claim_slack_message("C1", "1.2", "dollar_amount"))
            self.assertFalse(db.claim_slack_message("C1", "1.2", "dollar_amount"))
            db.mark_slack_message_acknowledged("C1", "1.2")
            self.assertFalse(db.claim_slack_message("C1", "1.2", "dollar_amount"))


if __name__ == "__main__":
    unittest.main()
