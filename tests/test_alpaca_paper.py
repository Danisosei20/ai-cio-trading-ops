from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
from robinhood_tools.cli import main as cli_main
from robinhood_tools.errors import AuthorizationRequired, PolicyViolation
from robinhood_tools.models import EquityOrderRequest, OptionOrderRequest
from robinhood_tools.risk import RiskLimits
from robinhood_tools.runtime import RuntimeSettings, build_mode_service
from robinhood_tools.universe import Sp500Snapshot


class FakeAlpacaPaperTransport:
    def __init__(self):
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if (method, path) == ("GET", "/v2/account"):
            return {
                "id": "paper-account-id", "account_number": "PA12345678", "status": "ACTIVE",
                "account_blocked": False, "trading_blocked": False,
            }
        if (method, path) == ("GET", "/v2/assets/AAPL"):
            return {
                "id": "asset-aapl", "symbol": "AAPL", "status": "active", "tradable": True,
                "fractionable": True, "class": "us_equity",
            }
        if (method, path) == ("POST", "/v2/orders"):
            return {
                "id": "paper-order-1", "account_id": "paper-account-id", "symbol": payload["symbol"],
                "side": payload["side"], "status": "accepted",
            }
        if (method, path) == ("GET", "/v2/orders/paper-order-1"):
            return {
                "id": "paper-order-1", "symbol": "AAPL", "side": "buy", "status": "new",
            }
        if (method, path) == ("DELETE", "/v2/orders/paper-order-1"):
            return {}
        if (method, path) == (
            "GET", "/v2/account/activities?activity_types=FILL&page_size=100&direction=desc",
        ):
            return [{
                "id": "fill-1", "order_id": "paper-order-1", "qty": "0.1", "price": "200",
                "transaction_time": "2026-07-14T14:00:00Z",
            }]
        if (method, path) == ("GET", "/v2/positions"):
            return [{"symbol": "AAPL", "qty": "0.1", "market_value": "20"}]
        if (method, path) == ("GET", "/v2/orders?status=open&limit=500&direction=asc"):
            return []
        raise AssertionError((method, path, payload))


class AlpacaPaperBackendTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeAlpacaPaperTransport()
        self.backend = AlpacaPaperBackend(self.transport)
        self.request = EquityOrderRequest(
            "paper-account-id", "AAPL", "buy", "market", "gfd", notional=Decimal("25"),
        )

    def test_http_transport_rejects_missing_credentials_and_live_url(self):
        with self.assertRaises(AuthorizationRequired):
            AlpacaPaperHttpTransport("", "")
        with self.assertRaisesRegex(PolicyViolation, "must remain true"):
            AlpacaPaperHttpTransport.from_values({
                "ALPACA_API_KEY": "paper-key", "ALPACA_SECRET_KEY": "paper-secret",
                "ALPACA_PAPER_TRADE": "false",
            })
        with self.assertRaisesRegex(PolicyViolation, "paper-api"):
            AlpacaPaperHttpTransport("paper-key", "paper-secret", base_url="https://api.alpaca.markets")

    def test_review_and_place_use_paper_order_contract(self):
        account = self.backend.list_accounts()[0]
        self.assertEqual(account.label, "Alpaca Paper")
        self.assertEqual(account.masked_account_number, "----5678")

        review = self.backend.review_equity_order(self.request)
        self.assertEqual(review.estimated_cost, Decimal("25"))
        self.assertEqual(review.raw["environment"], "paper")
        order = self.backend.place_equity_order(self.request, review.review_id)
        self.assertEqual(order.status, "confirmed")
        post = next(call for call in self.transport.calls if call[:2] == ("POST", "/v2/orders"))
        self.assertEqual(post[2]["notional"], "25")
        self.assertEqual(post[2]["time_in_force"], "day")
        self.assertEqual(post[2]["type"], "market")
        self.assertFalse(post[2]["extended_hours"])

    def test_changed_order_or_live_style_notional_order_fails_closed(self):
        review = self.backend.review_equity_order(self.request)
        changed = EquityOrderRequest(
            "paper-account-id", "AAPL", "buy", "market", "gfd", notional=Decimal("24"),
        )
        with self.assertRaisesRegex(PolicyViolation, "matching fresh"):
            self.backend.place_equity_order(changed, review.review_id)
        gtc = EquityOrderRequest(
            "paper-account-id", "AAPL", "buy", "market", "gtc", notional=Decimal("25"),
        )
        with self.assertRaisesRegex(PolicyViolation, "gfd/day"):
            self.backend.review_equity_order(gtc)

    def test_cancel_and_options_policy(self):
        cancelled = self.backend.cancel_equity_order("paper-order-1")
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(cancelled.account_id, "paper-account-id")
        with self.assertRaisesRegex(PolicyViolation, "Options are disabled"):
            self.backend.review_option_order(OptionOrderRequest("paper-account-id", (), "market", "gfd"))

    def test_order_status_fills_positions_and_open_orders_support_reconciliation(self):
        order = self.backend.get_order(account_id="paper-account-id", order_id="paper-order-1")
        fills = self.backend.get_fills(account_id="paper-account-id", order_id="paper-order-1")
        self.assertEqual(order.status, "queued")
        self.assertEqual((fills[0].fill_id, fills[0].quantity, fills[0].price),
                         ("fill-1", Decimal("0.1"), Decimal("200")))
        self.assertEqual(self.backend.list_positions()[0]["symbol"], "AAPL")
        self.assertEqual(self.backend.list_open_orders(), [])

    def test_mode_factory_routes_paper_to_alpaca_without_live_kill_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = RuntimeSettings(
                "paper_auto", False, "C1", 120, root / "paper.db", root / "paper.html", RiskLimits(),
            )
            universe = Sp500Snapshot(
                frozenset({"AAPL"}), datetime.now(timezone.utc).isoformat(), "https://www.spglobal.com/sp500",
            )
            service = build_mode_service(
                settings=settings, sp500_snapshot=universe, alpaca_transport=self.transport,
            )
            review = service.review_equity_order(self.request)
            self.assertTrue(review.review_id.startswith("alpaca-paper-review-"))

    def test_paper_health_without_credentials_is_clear_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = json.loads(Path("config/approval_routes.example.json").read_text(encoding="utf-8"))
            config["runtime"]["paper_database_path"] = str(root / "paper.db")
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            env_path = root / ".env"
            env_path.write_text(
                "TIMEZONE=America/New_York\nSLACK_CHANNEL_ID=C_TEST\n"
                "HEALTH_SLACK_CHANNEL_ID=C_HEALTH\nAPPROVAL_WINDOW_MINUTES=120\n"
                "ROBINHOOD_ACCOUNT_NICKNAME=Agentic\nINVESTMENT_OBJECTIVE=Test\n"
                "RISK_TOLERANCE=moderate\nTAX_CONTEXT=test\nTRADING_MODE=paper_auto\n"
                "TRADING_ENABLED=false\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with mock.patch.dict(os.environ, {"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": ""}), \
                    redirect_stdout(output):
                result = cli_main([
                    "--config", str(config_path), "--env-file", str(env_path), "paper-broker-health",
                ])
            payload = json.loads(output.getvalue())
            self.assertEqual(result, 2)
            self.assertFalse(payload["connected"])
            self.assertEqual(payload["environment"], "paper")
            self.assertIn("credentials", payload["error"])


if __name__ == "__main__":
    unittest.main()
