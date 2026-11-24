"""Regressions for Phase 7.2 adversarial review findings."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from robinhood_tools import authorization_keys as keys
from robinhood_tools import authorization_propagation as prop
from robinhood_tools import execution_authorization as ea

NOW = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
BASE = dict(os.environ, CIO_AUTH_ALLOW_EPHEMERAL_KEY="1",
            CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED="1",
            CIO_AUTH_ALLOW_PLAINTEXT_KEYSTORE="1",
            EXECUTION_AUTH_HMAC_KEY="review-fix-key-256-bit-test",
            EXECUTION_AUTHORIZATION_ENABLED="true")


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        (self.root / "outputs/paper").mkdir(parents=True)
        (self.root / "outputs/orchestrator/run-1").mkdir(parents=True)
        self.dec = {"run_id": "run-1", "verdict": "proceed",
                    "generated_at": "2026-11-23T09:45:00-05:00"}
        (self.root / "outputs/orchestrator/run-1/decision.json").write_text(
            json.dumps(self.dec), encoding="utf-8")
        (self.root / "outputs/orchestrator/latest.json").write_text(
            json.dumps(self.dec), encoding="utf-8")
        self.ev = self.root / "ev.json"
        self.ev.write_text("{}", encoding="utf-8")
        self.patcher = mock.patch.dict(os.environ, BASE, clear=True)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def test_h1_ephemeral_key_blocked_under_enforcement_without_override(self):
        env = dict(BASE)
        env.pop("CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(keys.ephemeral_key())

    def test_h2_revoked_key_cannot_sign(self):
        store = keys.KeyStore(self.root / "keys.json", allow_plaintext=True)
        kid = store.initialize()
        store.revoke(kid)
        env = dict(BASE)
        env.pop("CIO_AUTH_ALLOW_EPHEMERAL_KEY", None)
        env.pop("CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED", None)
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(keys, "get_store", return_value=store):
            with self.assertRaises(Exception):
                ea._sign({"state": "PENDING"})

    def test_h2_keyed_integrity_detects_status_tamper(self):
        store = keys.KeyStore(self.root / "keys.json", allow_plaintext=True)
        store.initialize()
        doc = json.loads(store.path.read_text(encoding="utf-8"))
        doc["keys"][0]["status"] = "REVOKED"
        store.path.write_text(json.dumps(doc), encoding="utf-8")
        self.assertFalse(store.verify_store())

    def test_h3_verify_only_key_cannot_sign(self):
        store = keys.KeyStore(self.root / "keys.json", allow_plaintext=True)
        kid = store.initialize()
        store.rotate()
        with self.assertRaises(keys.SecurityError):
            store.raw_key(kid)

    def test_m2_revoking_one_child_does_not_block_sibling(self):
        parent = self._parent()
        a = self._child(parent, "AAPL")
        b = self._child(parent, "MSFT")
        ea.revoke_authorization(a["authorization_id"], self.root, "test")
        ok, why = self._validate(b)
        self.assertTrue(ok, why)

    def test_m3_child_cannot_be_derived_from_revoked_parent(self):
        parent = self._parent()
        ea.revoke_parent(self.root, parent["authorization_id"], "test")
        with self.assertRaises(ea.AuthorizationError):
            self._child(parent, "AAPL")

    def test_m4_audit_chain_keyed_tamper_detected(self):
        from robinhood_tools import authorization_audit as audit
        path = self.root / "audit.jsonl"
        audit.emit("authorization_created", path=path, authorization_id="a")
        audit.emit("authorization_consumed", path=path, authorization_id="a")
        lines = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[0])
        row["outcome"] = "tampered"
        lines[0] = json.dumps(row, sort_keys=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertFalse(audit.verify_chain(path))

    def _parent(self):
        return ea.store_authorization(ea.create_authorization(
            decision=self.dec, root=self.root, artifacts=[self.ev],
            strategy_id="gate", symbol="SPY", side="buy", max_quantity=10,
            max_notional=10000, max_risk=500, broker="alpaca", account_id="acct",
            environment="paper", ttl_seconds=3600, now=NOW), self.root)

    def _child(self, parent, symbol):
        return prop.derive_and_store(parent=parent, root=self.root, symbol=symbol,
                                     side="buy", quantity=1, notional=100, risk=25,
                                     strategy_id="x", now=NOW)

    def _validate(self, child):
        return ea.validate_authorization(
            child, root=self.root, decision=self.dec, artifacts=[], strategy_id="x",
            symbol=child["symbol"], side="buy", quantity=1, notional=100, risk=25,
            broker="alpaca", account_id="acct", environment="paper", now=NOW)


if __name__ == "__main__":
    unittest.main()
