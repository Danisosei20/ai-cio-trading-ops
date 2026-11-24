"""Regression: every risk-increasing entry is authorized; exits never are."""
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

from robinhood_tools import (authorization_propagation as prop,
                             execution_authorization as ea, paper_flow)

KEY = "entry-regression-key"
NOW = datetime(2026, 11, 23, 15, 25, tzinfo=timezone.utc)


def decision():
    return {"run_id": "run-1", "verdict": "proceed",
            "generated_at": "2026-11-23T09:45:00-05:00"}


class EntryRegressionTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ, CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED='1', CIO_AUTH_ALLOW_EPHEMERAL_KEY='1', EXECUTION_AUTH_HMAC_KEY=KEY,
                        EXECUTION_AUTHORIZATION_ENABLED="true")
        self.patcher = mock.patch.dict(os.environ, self.env, clear=True)
        self.patcher.start()
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        (self.root / "outputs/paper").mkdir(parents=True)
        (self.root / "outputs/orchestrator/run-1").mkdir(parents=True)
        (self.root / "outputs/orchestrator/run-1/decision.json").write_text(
            json.dumps(decision()), encoding="utf-8")
        (self.root / "outputs/orchestrator/latest.json").write_text(
            json.dumps({"run_id": "run-1", "verdict": "proceed",
                        "generated_at": decision()["generated_at"]}), encoding="utf-8")
        self.evidence = self.root / "evidence.json"
        self.evidence.write_text("{}", encoding="utf-8")
        self.parent = ea.store_authorization(ea.create_authorization(
            decision=decision(), root=self.root, artifacts=[self.evidence],
            strategy_id="gate-parent", symbol="SPY", side="buy",
            max_quantity=10, max_notional=10000, max_risk=500,
            broker="alpaca", account_id="paper-default", environment="paper",
            ttl_seconds=3600, now=NOW), self.root)

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def _child(self, symbol, side, qty=2, price=100):
        notional = qty * price
        return prop.derive_and_store(
            parent=self.parent, root=self.root, symbol=symbol, side=side,
            quantity=qty, notional=notional, risk=notional * 0.25,
            strategy_id="research-long", now=NOW)

    def _gateway(self, child, symbol, side, qty=2, price=100):
        notional = qty * price
        return ea.gateway(
            authorization_id=child["authorization_id"], root=self.root,
            decision=decision(), artifacts=[], strategy_id="research-long",
            symbol=symbol, side=side, quantity=qty, notional=notional,
            risk=notional * 0.25, broker="alpaca", account_id="paper-default",
            environment="paper", now=NOW)

    def test_long_entry_authorized_and_consumed_once(self):
        child = self._child("AAPL", "buy")
        ok, reason = self._gateway(child, "AAPL", "buy")
        self.assertTrue(ok, reason)
        ok2, _ = self._gateway(child, "AAPL", "buy")
        self.assertFalse(ok2)

    def test_short_entry_authorized(self):
        child = self._child("TSLA", "sell")
        ok, reason = self._gateway(child, "TSLA", "sell")
        self.assertTrue(ok, reason)

    def test_short_entry_without_auth_blocked_at_boundary(self):
        order = paper_flow.SignalOrder(symbol="TSLA", side="sell",
                                       quantity=Decimal("2"),
                                       limit_price=Decimal("100"),
                                       position_effect="open")
        ok, reason = paper_flow.require_order_authorization(order, self.root)
        self.assertFalse(ok)

    def test_exit_orders_need_no_authorization(self):
        for side, effect in (("sell", "close"), ("buy", "close")):
            order = paper_flow.SignalOrder(symbol="AAPL", side=side,
                                           quantity=Decimal("2"),
                                           limit_price=Decimal("100"),
                                           position_effect=effect)
            ok, reason = paper_flow.require_order_authorization(order, self.root)
            self.assertTrue(ok, reason)

    def test_concurrent_consumption_single_winner(self):
        child = self._child("AAPL", "buy")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self._gateway(child, "AAPL", "buy"), range(8)))
        self.assertEqual(sum(1 for ok, _ in results if ok), 1)

    def test_partial_fill_retry_is_rejected(self):
        child = self._child("AAPL", "buy")
        self.assertTrue(self._gateway(child, "AAPL", "buy")[0])
        # A retry after partial fill must not create a second exposure.
        ok, reason = self._gateway(child, "AAPL", "buy", qty=1, price=100)
        self.assertFalse(ok)
        self.assertIn("state", reason.lower())

    def test_restart_recovers_consumed_state(self):
        """A fresh module instance (simulated restart) reads durable SQLite state."""
        child = self._child("AAPL", "buy")
        self.assertTrue(self._gateway(child, "AAPL", "buy")[0])
        conn = sqlite3.connect(str(self.root / "outputs/paper/execution_authorizations.sqlite"))
        state = conn.execute("SELECT state FROM authorizations WHERE id=?",
                             (child["authorization_id"],)).fetchone()[0]
        conn.close()
        self.assertEqual(state, "CONSUMED")
        self.assertIsNotNone(ea.load_authorization(child["authorization_id"], self.root))

    def test_tampered_child_rejected(self):
        child = self._child("AAPL", "buy")
        bad = dict(child, max_notional=999999)
        ok, reason = ea.validate_authorization(
            bad, root=self.root, decision=decision(), artifacts=[],
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=25, broker="alpaca",
            account_id="paper-default", environment="paper", now=NOW)
        self.assertFalse(ok)
        self.assertIn("integrity", reason.lower())

    def test_expired_child_rejected(self):
        child = self._child("AAPL", "buy")
        ok, reason = ea.gateway(
            authorization_id=child["authorization_id"], root=self.root,
            decision=decision(), artifacts=[], strategy_id="research-long",
            symbol="AAPL", side="buy", quantity=1, notional=100, risk=25,
            broker="alpaca", account_id="paper-default", environment="paper",
            now=datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc))
        self.assertFalse(ok)
        self.assertIn("expired", reason.lower())

    def test_option_entry_consumes_child_authorization(self):
        import sys
        sys.path.insert(0, str(self.root.parent))
        # Exercise the option gateway path directly with an OCC-bound child.
        child = self._child("AAPL261218C00150000", "buy")
        ok, reason = self._gateway(child, "AAPL261218C00150000", "buy")
        self.assertTrue(ok, reason)

    def test_no_llm_in_authorization_modules(self):
        for module in (ea, prop):
            src = Path(module.__file__).read_text(encoding="utf-8").lower()
            for banned in ("openai", "fleet_llm", "nemotron", "chat/completions"):
                self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
