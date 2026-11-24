"""Regressions for adversarial review findings F1-F5."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from robinhood_tools import authorization_propagation as prop
from robinhood_tools import execution_authorization as ea

KEY = "review-fix-key"
NOW = datetime(2026, 11, 23, 14, 45, tzinfo=timezone.utc)


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ, EXECUTION_AUTH_HMAC_KEY=KEY,
                        EXECUTION_AUTHORIZATION_ENABLED="true",
                        ALPACA_ACCOUNT_ID="real-paper-account")
        self.patcher = mock.patch.dict(os.environ, self.env, clear=True)
        self.patcher.start()
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        (self.root / "outputs/paper").mkdir(parents=True)
        (self.root / "outputs/orchestrator/run-1").mkdir(parents=True)
        self.dec = {"run_id": "run-1", "verdict": "proceed",
                    "generated_at": "2026-11-23T09:45:00-05:00"}
        (self.root / "outputs/orchestrator/run-1/decision.json").write_text(
            json.dumps(self.dec), encoding="utf-8")
        (self.root / "outputs/orchestrator/latest.json").write_text(
            json.dumps({**self.dec, "account_id": "real-paper-account"}), encoding="utf-8")
        self.ev = self.root / "evidence.json"
        self.ev.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def _parent(self, **kw):
        args = dict(decision=self.dec, root=self.root, artifacts=[self.ev],
                    strategy_id="gate-parent", symbol="SPY", side="buy",
                    max_quantity=10, max_notional=10000, max_risk=500,
                    broker="alpaca", account_id="real-paper-account",
                    environment="paper", ttl_seconds=3600, now=NOW)
        args.update(kw)
        return ea.store_authorization(ea.create_authorization(**args), self.root)

    def test_f1_default_parent_lasts_through_session(self):
        from scripts import execution_handoff as h
        parent = h.issue_authorization(
            root=self.root, decision=self.dec, strategy_id="gate", symbol="SPY",
            side="buy", max_quantity=10, max_notional=10000, max_risk=500,
            broker="alpaca", account_id="real-paper-account", now=NOW)
        expires = datetime.fromisoformat(parent["expires_at"])
        self.assertGreater(expires, datetime(2026, 11, 23, 20, 45, tzinfo=timezone.utc))

    def test_f2_main_wires_parent_to_daytrade_engine(self):
        src = Path("scripts/daytrade_engine.py").read_text(encoding="utf-8")
        self.assertIn("authorization_id=_parent_authorization_id()", src)

    def test_f3_exit_orders_are_marked_close(self):
        for rel in ("scripts/daytrade_engine.py", "scripts/tradingagents_paper_auto.py",
                    "scripts/flatten_daytrades.py", "scripts/paper_position_guard.py",
                    "scripts/overnight_pilot.py"):
            src = Path(rel).read_text(encoding="utf-8")
            self.assertIn('position_effect="close"', src, rel)

    def test_f3_sell_to_close_does_not_get_authorization_id(self):
        src = Path("scripts/daytrade_engine.py").read_text(encoding="utf-8")
        self.assertIn('plan.get("direction") in ("long", "short")', src)

    def test_f4_child_cannot_exceed_parent_limits(self):
        parent = self._parent(max_quantity=2, max_notional=100, max_risk=10)
        with self.assertRaises(ea.AuthorizationError):
            prop.child_authorization(parent=parent, root=self.root, symbol="AAPL",
                                     side="buy", quantity=5, notional=500, risk=50,
                                     strategy_id="x", now=NOW)

    def test_f5_gateway_uses_child_account_not_placeholder(self):
        from robinhood_tools import paper_flow
        from datetime import datetime as _dt, timezone as _tz
        current = _dt.now(_tz.utc)
        parent = self._parent(now=current, ttl_seconds=3600)
        child = prop.derive_and_store(
            parent=parent, root=self.root, symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=25, strategy_id="x", now=current)
        order = paper_flow.SignalOrder(symbol="AAPL", side="buy",
                                       quantity=__import__("decimal").Decimal("1"),
                                       limit_price=__import__("decimal").Decimal("100"),
                                       authorization_id=child["authorization_id"])
        ok, why = paper_flow.require_order_authorization(order, self.root)
        self.assertTrue(ok, why)

    def test_f4_child_cap_enforced(self):
        parent = self._parent(max_quantity=100, max_notional=100000, max_risk=10000)
        for i in range(prop.MAX_CHILDREN_PER_PARENT):
            prop.derive_and_store(
                parent=parent, root=self.root, symbol=f"S{i}", side="buy",
                quantity=1, notional=10, risk=1, strategy_id="x", now=NOW)
        with self.assertRaises(ea.AuthorizationError):
            prop.child_authorization(parent=parent, root=self.root, symbol="OVER",
                                     side="buy", quantity=1, notional=10, risk=1,
                                     strategy_id="x", now=NOW)


if __name__ == "__main__":
    unittest.main()
