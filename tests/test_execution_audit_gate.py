"""Audit availability must fail closed for risk-increasing entries."""
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from robinhood_tools import execution_audit_gate as gate
from robinhood_tools import paper_flow

ENV = dict(os.environ, EXECUTION_AUTHORIZATION_ENABLED="true",
           CIO_AUTH_ALLOW_EPHEMERAL_KEY="1",
           CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED="1",
           EXECUTION_AUTH_HMAC_KEY="audit-gate-test-key-32-bytes-long",
           CIO_AUTH_ALLOW_PLAINTEXT_KEYSTORE="1")


class AuditGateTests(unittest.TestCase):
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

    def tearDown(self):
        self.t.cleanup()

    def test_missing_audit_store_blocks_entries(self):
        order = paper_flow.SignalOrder(symbol="AAPL", side="buy",
                                       quantity=__import__("decimal").Decimal("1"),
                                       limit_price=__import__("decimal").Decimal("100"),
                                       position_effect="open", authorization_id="auth-x")
        with mock.patch.dict(os.environ, ENV, clear=True):
            ok, reason = paper_flow.require_order_authorization(order, self.root)
        self.assertFalse(ok)
        self.assertIn("audit", reason.lower())

    def test_corrupt_audit_chain_blocks_entries(self):
        audit_file = self.root / "outputs/paper/authorization_audit.jsonl"
        audit_file.write_text("{bad json}\n", encoding="utf-8")
        ok, reason = gate.audit_health(self.root)
        self.assertFalse(ok)

    def test_healthy_audit_store_passes(self):
        from robinhood_tools import authorization_audit as audit
        with mock.patch.dict(os.environ, ENV, clear=True):
            audit.emit("authorization_created",
                       path=self.root / "outputs/paper/authorization_audit.jsonl",
                       authorization_id="seed")
        with mock.patch.dict(os.environ, ENV, clear=True):
            ok, reason = gate.audit_health(self.root)
        self.assertTrue(ok, reason)

    def test_exit_order_unaffected_by_unhealthy_audit(self):
        order = paper_flow.SignalOrder(symbol="AAPL", side="sell",
                                       quantity=__import__("decimal").Decimal("1"),
                                       limit_price=__import__("decimal").Decimal("100"),
                                       position_effect="close")
        with mock.patch.dict(os.environ, ENV, clear=True):
            ok, reason = paper_flow.require_order_authorization(order, self.root)
        self.assertTrue(ok, reason)

    def test_alert_file_written_on_failure(self):
        gate.emit_alert(self.root, "audit_unavailable", "chain invalid")
        p = self.root / "outputs/paper/authorization_audit_alerts.jsonl"
        self.assertTrue(p.exists())
        self.assertIn("audit_unavailable", p.read_text(encoding="utf-8"))

    def test_submission_intent_state_machine(self):
        a = gate.open_intent(self.root, "auth-1", "AAPL", "buy", 2, 200.0)
        self.assertEqual(a["state"], "INTENDED")
        gate.mark_submitted(self.root, "auth-1", "order-1")
        gate.mark_uncertain(self.root, "auth-1")
        pending = gate.pending_reconciliation(self.root)
        self.assertIn("auth-1", [p["authorization_id"] for p in pending])
        gate.mark_reconciled(self.root, "auth-1", "filled")
        self.assertEqual(gate.pending_reconciliation(self.root), [])


if __name__ == "__main__":
    unittest.main()
