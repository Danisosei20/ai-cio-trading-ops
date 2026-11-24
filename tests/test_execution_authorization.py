"""Deterministic execution authorization: integrity, binding, expiry, atomicity."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from robinhood_tools import execution_authorization as ea

KEY = "test-hmac-key-not-a-real-credential"


def _decision(verdict="proceed", run_id="run-1"):
    return {"run_id": run_id, "verdict": verdict,
            "generated_at": "2026-11-23T09:45:00-05:00"}


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        (self.root / "outputs/paper").mkdir(parents=True)
        self.env = dict(os.environ, EXECUTION_AUTH_HMAC_KEY=KEY)
        self.patcher = mock.patch.dict(os.environ, self.env, clear=True)
        self.patcher.start()
        self.artifacts = self.root / "artifact.json"
        self.artifacts.write_text(json.dumps({"ranked": [{"symbol": "AAPL"}]}), encoding="utf-8")
        self.now = datetime(2026, 11, 23, 14, 20, tzinfo=timezone.utc)

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def _make(self, **overrides):
        kwargs = dict(
            decision=_decision(), root=self.root, artifacts=[self.artifacts],
            strategy_id="research-long", symbol="AAPL", side="buy",
            max_quantity=10, max_notional=5000, max_risk=25,
            broker="alpaca", account_id="paper-acct-1", environment="paper",
            ttl_seconds=300, policy_version="risk-2026-11-23", now=self.now)
        kwargs.update(overrides)
        return ea.create_authorization(**kwargs)

    # --- integrity -----------------------------------------------------
    def test_created_authorization_has_required_fields(self):
        a = self._make()
        for field in ea.REQUIRED_FIELDS:
            self.assertIn(field, a)

    def test_signature_is_present_and_verifies(self):
        a = self._make()
        self.assertTrue(a["signature"])
        self.assertTrue(ea.verify_integrity(a))

    def test_tampered_authorization_is_rejected(self):
        a = self._make()
        a["max_quantity"] = 9999
        self.assertFalse(ea.verify_integrity(a))

    def test_missing_key_fails_closed(self):
        a = self._make()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ea.verify_integrity(a))

    def test_artifact_hash_changes_detected(self):
        a = self._make()
        self.artifacts.write_text(json.dumps({"ranked": [{"symbol": "TSLA"}]}), encoding="utf-8")
        ok, reason = ea.validate_authorization(
            a, root=self.root, decision=_decision(),
            artifacts=[self.artifacts], strategy_id="research-long",
            symbol="AAPL", side="buy", quantity=1, notional=100, risk=5,
            broker="alpaca", account_id="paper-acct-1", environment="paper", now=self.now)
        self.assertFalse(ok)
        self.assertIn("hash", reason.lower())

    # --- binding -------------------------------------------------------
    def test_valid_authorization_passes(self):
        a = self._make()
        ok, reason = ea.validate_authorization(
            a, root=self.root, decision=_decision(), artifacts=[self.artifacts],
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=5, notional=1000, risk=10, broker="alpaca",
            account_id="paper-acct-1", environment="paper", now=self.now)
        self.assertTrue(ok, reason)

    def test_halt_verdict_authorization_rejected_at_creation(self):
        with self.assertRaises(ea.AuthorizationError):
            self._make(decision=_decision(verdict="halt"))

    def test_wrong_account_rejected(self):
        a = self._make()
        ok, reason = ea.validate_authorization(
            a, root=self.root, decision=_decision(), artifacts=[self.artifacts],
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=5, broker="alpaca",
            account_id="wrong-account", environment="paper", now=self.now)
        self.assertFalse(ok)
        self.assertIn("account", reason.lower())

    def test_wrong_symbol_rejected(self):
        a = self._make()
        ok, reason = ea.validate_authorization(
            a, root=self.root, decision=_decision(), artifacts=[self.artifacts],
            strategy_id="research-long", symbol="TSLA", side="buy",
            quantity=1, notional=100, risk=5, broker="alpaca",
            account_id="paper-acct-1", environment="paper", now=self.now)
        self.assertFalse(ok)
        self.assertIn("symbol", reason.lower())

    def test_wrong_side_rejected(self):
        a = self._make()
        ok, reason = ea.validate_authorization(
            a, root=self.root, decision=_decision(), artifacts=[self.artifacts],
            strategy_id="research-long", symbol="AAPL", side="sell",
            quantity=1, notional=100, risk=5, broker="alpaca",
            account_id="paper-acct-1", environment="paper", now=self.now)
        self.assertFalse(ok)
        self.assertIn("side", reason.lower())

    def test_excessive_quantity_rejected(self):
        a = self._make()
        ok, reason = ea.validate_authorization(
            a, root=self.root, decision=_decision(), artifacts=[self.artifacts],
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=11, notional=100, risk=5, broker="alpaca",
            account_id="paper-acct-1", environment="paper", now=self.now)
        self.assertFalse(ok)
        self.assertIn("quantity", reason.lower())

    def test_live_environment_rejected(self):
        a = self._make()
        ok, reason = ea.validate_authorization(
            a, root=self.root, decision=_decision(), artifacts=[self.artifacts],
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=5, broker="alpaca",
            account_id="paper-acct-1", environment="live", now=self.now)
        self.assertFalse(ok)
        self.assertIn("environment", reason.lower())

    # --- expiry / state ------------------------------------------------
    def test_expired_authorization_rejected(self):
        a = self._make()
        ok, reason = ea.validate_authorization(
            a, root=self.root, decision=_decision(), artifacts=[self.artifacts],
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=5, broker="alpaca",
            account_id="paper-acct-1", environment="paper",
            now=self.now + timedelta(seconds=301))
        self.assertFalse(ok)
        self.assertIn("expired", reason.lower())

    def test_terminal_state_rejected(self):
        a = ea._sign({**self._make(), "state": "CONSUMED"})
        ok, reason = ea.validate_authorization(
            a, root=self.root, decision=_decision(), artifacts=[self.artifacts],
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=5, broker="alpaca",
            account_id="paper-acct-1", environment="paper", now=self.now)
        self.assertFalse(ok)
        self.assertIn("state", reason.lower())

    def test_invalid_transition_rejected(self):
        self.assertFalse(ea.valid_transition("CONSUMED", "AUTHORIZED"))
        self.assertFalse(ea.valid_transition("REJECTED", "PENDING"))
        self.assertTrue(ea.valid_transition("PENDING", "VALIDATED"))
        self.assertTrue(ea.valid_transition("AUTHORIZED", "CONSUMED"))

    # --- atomic consumption --------------------------------------------
    def test_atomic_reservation_single_winner(self):
        a = self._make()
        ea.store_authorization(a, self.root)
        self.assertTrue(ea.reserve_authorization(a["authorization_id"], self.root,
                                                  now=self.now))
        self.assertFalse(ea.reserve_authorization(a["authorization_id"], self.root,
                                                   now=self.now))
        self.assertEqual(ea.load_authorization(a["authorization_id"], self.root)["state"],
                         "CONSUMED")

    def test_replay_of_consumed_authorization_blocked(self):
        a = self._make()
        ea.store_authorization(a, self.root)
        ea.reserve_authorization(a["authorization_id"], self.root, now=self.now)
        ok, reason = ea.gateway(
            authorization_id=a["authorization_id"], root=self.root,
            decision=_decision(), artifacts=[self.artifacts],
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=5, broker="alpaca",
            account_id="paper-acct-1", environment="paper", now=self.now)
        self.assertFalse(ok)
        self.assertIn("state", reason.lower())

    def test_gateway_passes_valid_authorization(self):
        a = self._make()
        ea.store_authorization(a, self.root)
        ok, reason = ea.gateway(
            authorization_id=a["authorization_id"], root=self.root,
            decision=_decision(), artifacts=[self.artifacts],
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=5, broker="alpaca",
            account_id="paper-acct-1", environment="paper", now=self.now)
        self.assertTrue(ok, reason)
        self.assertEqual(ea.load_authorization(a["authorization_id"], self.root)["state"],
                         "CONSUMED")

    def test_concurrent_workers_cannot_both_consume(self):
        from concurrent.futures import ThreadPoolExecutor

        a = self._make()
        ea.store_authorization(a, self.root)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _: ea.reserve_authorization(a["authorization_id"], self.root,
                                                   now=self.now), range(8)))
        self.assertEqual(sum(bool(r) for r in results), 1)

    def test_gateway_is_fail_closed_without_authorization(self):
        ok, reason = ea.gateway(
            authorization_id="missing", root=self.root,
            decision=_decision(), artifacts=[self.artifacts],
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=1, notional=100, risk=5, broker="alpaca",
            account_id="paper-acct-1", environment="paper", now=self.now)
        self.assertFalse(ok)

    def test_enforcement_flag_defaults_off(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ea.enforcement_enabled())

    def test_enforcement_flag_requires_exact_true(self):
        with mock.patch.dict(os.environ, {"EXECUTION_AUTHORIZATION_ENABLED": "TRUE"}, clear=True):
            self.assertFalse(ea.enforcement_enabled())
        with mock.patch.dict(os.environ, {"EXECUTION_AUTHORIZATION_ENABLED": "true"}, clear=True):
            self.assertTrue(ea.enforcement_enabled())

    def test_no_llm_dependency(self):
        src = Path(ea.__file__).read_text(encoding="utf-8")
        for banned in ("openai", "fleet_llm", "nemotron", "chat/completions"):
            self.assertNotIn(banned, src.lower())


if __name__ == "__main__":
    unittest.main()
