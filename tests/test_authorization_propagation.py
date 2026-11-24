"""Per-order child authorizations derived from the 09:45 gate authorization."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from robinhood_tools import authorization_propagation as ap
from robinhood_tools import execution_authorization as ea

KEY = "propagation-test-key"
NOW = datetime(2026, 11, 23, 15, 20, tzinfo=timezone.utc)


class PropagationTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        (self.root / "outputs/paper").mkdir(parents=True)
        (self.root / "outputs/orchestrator/run-1").mkdir(parents=True)
        (self.root / "outputs/orchestrator/run-1/decision.json").write_text(
            json.dumps({"run_id": "run-1", "verdict": "proceed",
                        "generated_at": "2026-11-23T09:45:00-05:00"}), encoding="utf-8")
        self.artifact = self.root / "evidence.json"
        self.artifact.write_text('{"ok": true}', encoding="utf-8")
        self.env = dict(os.environ, CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED='1', CIO_AUTH_ALLOW_EPHEMERAL_KEY='1', EXECUTION_AUTH_HMAC_KEY=KEY,
                        EXECUTION_AUTHORIZATION_ENABLED="true")
        self.patcher = mock.patch.dict(os.environ, self.env, clear=True)
        self.patcher.start()
        self.parent = ea.store_authorization(ea.create_authorization(
            decision={"run_id": "run-1", "verdict": "proceed",
                      "generated_at": "2026-11-23T09:45:00-05:00"},
            root=self.root, artifacts=[self.artifact], strategy_id="gate-parent",
            symbol="SPY", side="buy", max_quantity=10, max_notional=10000,
            max_risk=500, broker="alpaca", account_id="acct-1",
            environment="paper", ttl_seconds=3600, now=NOW), self.root)

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def _child(self, **kw):
        args = dict(parent=self.parent, root=self.root, symbol="AAPL", side="buy",
                    quantity=2, notional=300.0, risk=15.0,
                    strategy_id="research-long", now=NOW)
        args.update(kw)
        return ap.child_authorization(**args)

    def test_child_inherits_decision_identity_and_evidence(self):
        child = self._child()
        self.assertEqual(child["decision_id"], self.parent["decision_id"])
        self.assertEqual(child["gate_run_id"], self.parent["gate_run_id"])
        self.assertEqual(child["artifact_hashes"], self.parent["artifact_hashes"])
        self.assertEqual(child["strategy_id"], "research-long")

    def test_child_binds_actual_order_fields(self):
        child = self._child()
        self.assertEqual(child["symbol"], "AAPL")
        self.assertEqual(child["side"], "buy")
        self.assertEqual(child["max_quantity"], 2)
        self.assertEqual(child["max_notional"], 300.0)
        self.assertEqual(child["position_effect"], "open")

    def test_child_expiry_never_exceeds_parent(self):
        child = self._child(ttl_seconds=99999)
        self.assertLessEqual(
            datetime.fromisoformat(child["expires_at"]),
            datetime.fromisoformat(self.parent["expires_at"]))

    def test_tampered_parent_cannot_derive_child(self):
        bad = dict(self.parent, max_quantity=99)
        with self.assertRaises(ea.AuthorizationError):
            self._child(parent=bad)

    def test_expired_parent_cannot_derive_child(self):
        expired = dict(self.parent, expires_at=(NOW - timedelta(seconds=1)).isoformat())
        expired = ea._sign(expired)
        with self.assertRaises(ea.AuthorizationError):
            self._child(parent=expired)

    def test_child_is_stored_and_consumable_once(self):
        child = ap.derive_and_store(**dict(
            parent=self.parent, root=self.root, symbol="AAPL", side="buy",
            quantity=2, notional=300.0, risk=15.0,
            strategy_id="research-long", now=NOW))
        ok, reason = ea.gateway(
            authorization_id=child["authorization_id"], root=self.root,
            decision={"run_id": "run-1", "verdict": "proceed",
                      "generated_at": "2026-11-23T09:45:00-05:00"},
            artifacts=[], strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=2, notional=300.0, risk=15.0, broker="alpaca",
            account_id="acct-1", environment="paper", now=NOW)
        self.assertTrue(ok, reason)
        ok2, _ = ea.gateway(
            authorization_id=child["authorization_id"], root=self.root,
            decision={"run_id": "run-1", "verdict": "proceed",
                      "generated_at": "2026-11-23T09:45:00-05:00"},
            artifacts=[], strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=2, notional=300.0, risk=15.0, broker="alpaca",
            account_id="acct-1", environment="paper", now=NOW)
        self.assertFalse(ok2)

    def test_child_rejects_wrong_order_fields(self):
        child = self._child()
        ok, reason = ea.validate_authorization(
            child, root=self.root,
            decision={"run_id": "run-1", "verdict": "proceed",
                      "generated_at": "2026-11-23T09:45:00-05:00"},
            artifacts=[], strategy_id="research-long", symbol="TSLA", side="buy",
            quantity=2, notional=300.0, risk=15.0, broker="alpaca",
            account_id="acct-1", environment="paper", now=NOW)
        self.assertFalse(ok)
        self.assertIn("symbol", reason.lower())


if __name__ == "__main__":
    unittest.main()
