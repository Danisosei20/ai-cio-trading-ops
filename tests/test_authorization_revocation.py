"""Durable revocation, cascade and replay protection."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from robinhood_tools import authorization_propagation as prop
from robinhood_tools import execution_authorization as ea

ENV = dict(os.environ, CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED='1', CIO_AUTH_ALLOW_EPHEMERAL_KEY="1",
           EXECUTION_AUTH_HMAC_KEY="revoke-key-long-test-key-32",
           EXECUTION_AUTHORIZATION_ENABLED="true")
NOW = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)


class RevocationTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        (self.root/"outputs/paper").mkdir(parents=True)
        (self.root/"outputs/orchestrator/run-1").mkdir(parents=True)
        self.dec = {"run_id": "run-1", "verdict": "proceed",
                    "generated_at": "2026-11-23T09:45:00-05:00"}
        (self.root/"outputs/orchestrator/run-1/decision.json").write_text(
            json.dumps(self.dec), encoding="utf-8")
        (self.root/"outputs/orchestrator/latest.json").write_text(json.dumps(self.dec), encoding="utf-8")
        self.ev = self.root / "ev.json"
        self.ev.write_text("{}", encoding="utf-8")
        self.patcher = mock.patch.dict(os.environ, ENV, clear=True)
        self.patcher.start()
        self.parent = ea.store_authorization(ea.create_authorization(
            decision=self.dec, root=self.root, artifacts=[self.ev],
            strategy_id="gate", symbol="SPY", side="buy", max_quantity=10,
            max_notional=10000, max_risk=500, broker="alpaca", account_id="acct",
            environment="paper", ttl_seconds=3600, now=NOW), self.root)

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def _child(self, symbol="AAPL"):
        return prop.derive_and_store(parent=self.parent, root=self.root, symbol=symbol,
                                     side="buy", quantity=1, notional=100, risk=25,
                                     strategy_id="x", now=NOW)

    def _validate(self, child):
        return ea.validate_authorization(
            child, root=self.root, decision=self.dec, artifacts=[],
            strategy_id="x", symbol=child["symbol"], side="buy", quantity=1,
            notional=100, risk=25, broker="alpaca", account_id="acct",
            environment="paper", now=NOW)

    def test_child_valid_before_revocation(self):
        self.assertTrue(self._validate(self._child())[0])

    def test_revoke_child_blocks_it(self):
        child = self._child()
        self.assertTrue(ea.revoke_authorization(child["authorization_id"], self.root, "test"))
        ok, reason = self._validate(child)
        self.assertFalse(ok)
        self.assertIn("revoked", reason.lower())

    def test_revoke_parent_cascades_to_children(self):
        child = self._child()
        ea.revoke_parent(self.root, self.parent["authorization_id"], "test")
        ok, reason = self._validate(child)
        self.assertFalse(ok)
        self.assertTrue(ea.is_revoked(self.parent["authorization_id"], self.root))

    def test_revoke_by_key_blocks_authorizations(self):
        child = self._child()
        kid = self.parent["key_id"]
        ea.revoke_by_key(self.root, kid, "compromised")
        self.assertFalse(self._validate(child)[0])

    def test_global_entry_revocation_blocks_children(self):
        child = self._child()
        ea.revoke_all_entries(self.root, "emergency")
        self.assertFalse(self._validate(child)[0])

    def test_revocation_survives_store_reload(self):
        child = self._child()
        ea.revoke_authorization(child["authorization_id"], self.root, "test")
        self.assertTrue(ea.is_revoked(child["authorization_id"], self.root))

    def test_revoked_authorization_cannot_be_consumed(self):
        child = self._child()
        ea.revoke_authorization(child["authorization_id"], self.root, "test")
        self.assertFalse(ea.reserve_authorization(child["authorization_id"], self.root, now=NOW))


if __name__ == "__main__":
    unittest.main()
