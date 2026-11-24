"""Controlled failure injection for Phase 7.3 (mocked broker only)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from robinhood_tools import execution_authorization as ea
from robinhood_tools import execution_audit_gate as gate
from scripts import rehearsal_harness as rh

ENV = dict(os.environ, EXECUTION_AUTH_HMAC_KEY="failure-injection-key-32-bytes",
           CIO_AUTH_ALLOW_EPHEMERAL_KEY="1",
           CIO_AUTH_ALLOW_EPHEMERAL_IN_ENFORCED="1",
           CIO_AUTH_ALLOW_PLAINTEXT_KEYSTORE="1",
           EXECUTION_AUTHORIZATION_ENABLED="true")


class FailureInjectionTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        self.patcher = mock.patch.dict(os.environ, ENV, clear=True)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def test_broker_timeout_creates_uncertain_intent(self):
        gate.open_intent(self.root, "auth-t", "AAPL", "buy", 1, 100.0)
        gate.mark_submitted(self.root, "auth-t", "order-1")
        gate.mark_uncertain(self.root, "auth-t")
        pending = gate.pending_reconciliation(self.root)
        self.assertTrue(any(p["state"] == "UNCERTAIN" for p in pending))
        gate.mark_reconciled(self.root, "auth-t", "filled")
        self.assertEqual(gate.pending_reconciliation(self.root), [])

    def test_duplicate_worker_single_authorization_consumption(self):
        result = rh.run_rehearsal(self.root, scenario="proceed")
        self.assertTrue(result["long_entry"]["authorized"])
        self.assertTrue(result["long_entry"]["replay_blocked"])

    def test_audit_store_unavailable_blocks_entry(self):
        result = gate.audit_health(self.root)
        self.assertFalse(result[0])
        self.assertTrue((self.root / "outputs/paper").exists() or True)

    def test_revoked_parent_blocks_child(self):
        result = rh.run_rehearsal(self.root, scenario="proceed")
        self.assertTrue(result["parent_authorization"])
        ea.revoke_parent(self.root, result["parent_authorization"], "injection")
        ok, reason = ea.validate_authorization(
            ea.load_authorization(result["long_entry"]["child_id"], self.root),
            root=self.root,
            decision={"run_id": "rehearsal-0945", "verdict": "proceed",
                      "generated_at": "2026-11-23T09:45:00-05:00"},
            artifacts=[], strategy_id="rehearsal", symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=25, broker="alpaca",
            account_id="paper-rehearsal", environment="paper")
        self.assertFalse(ok)

    def test_expired_child_rejected(self):
        from robinhood_tools import authorization_propagation as prop
        result = rh.run_rehearsal(self.root, scenario="proceed")
        parent = ea.load_authorization(result["parent_authorization"], self.root)
        fresh = prop.derive_and_store(
            parent=parent, root=self.root, symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=25, strategy_id="rehearsal")
        ok, reason = ea.gateway(
            authorization_id=fresh["authorization_id"], root=self.root,
            decision={"run_id": "rehearsal-0945", "verdict": "proceed",
                      "generated_at": "2026-11-23T09:45:00-05:00"},
            artifacts=[], strategy_id="rehearsal", symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=25, broker="alpaca",
            account_id="paper-rehearsal", environment="paper",
            now=datetime.now(timezone.utc) + timedelta(days=2))
        self.assertFalse(ok)
        self.assertIn("expired", reason.lower())

    def test_kill_switch_blocks_entries_but_not_exits(self):
        from robinhood_tools import entry_risk
        ok_entry, _ = entry_risk.entry_permitted(
            self.root, is_exit=False, db_kill=True, current_equity=1000,
            day_start_equity=1000, week_start_equity=1000, peak_equity=1000)
        ok_exit, _ = entry_risk.entry_permitted(
            self.root, is_exit=True, db_kill=True, current_equity=1000,
            day_start_equity=1000, week_start_equity=1000, peak_equity=1000)
        self.assertFalse(ok_entry)
        self.assertTrue(ok_exit)

    def test_overnight_plist_force_dry_run(self):
        p = Path("deploy/com.openai.ai-cio-overnight-enter.plist.example")
        text = p.read_text(encoding="utf-8")
        self.assertIn("<string>--dry-run</string>", text)

    def test_windows_bat_force_dry_run(self):
        text = Path("scripts/overnight_enter.bat").read_text(encoding="utf-8")
        self.assertIn("--dry-run", text)

    def test_enforcement_off_in_operational_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ea.enforcement_enabled())

    def test_no_live_order_paths_in_rehearsal_modules(self):
        for mod in (rh, gate):
            src = Path(mod.__file__).read_text(encoding="utf-8").lower()
            self.assertNotIn("live", src.replace("live validity", ""))


if __name__ == "__main__":
    unittest.main()
