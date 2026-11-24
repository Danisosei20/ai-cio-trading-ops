"""09:45 verdict -> bounded 10:20 execution authorization handoff."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts import execution_handoff as eh

KEY = "phase7-handoff-key"
NOW = datetime(2026, 11, 23, 14, 45, tzinfo=timezone.utc)


def decision(verdict="proceed", run_id="run-0945"):
    return {"run_id": run_id, "verdict": verdict,
            "generated_at": "2026-11-23T09:45:00-05:00"}


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        (self.root / "outputs/paper").mkdir(parents=True)
        (self.root / "outputs/orchestrator" / "run-0945").mkdir(parents=True)
        (self.root / "outputs/orchestrator/run-0945/decision.json").write_text(
            json.dumps(decision()), encoding="utf-8")
        self.env = dict(os.environ, EXECUTION_AUTH_HMAC_KEY=KEY,
                        EXECUTION_AUTHORIZATION_ENABLED="true")
        self.patcher = mock.patch.dict(os.environ, self.env, clear=True)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.t.cleanup()

    def _issue(self, **kw):
        args = dict(root=self.root, decision=decision(), strategy_id="research-long",
                    symbol="AAPL", side="buy", max_quantity=5, max_notional=1000,
                    max_risk=25, broker="alpaca", account_id="paper-acct",
                    ttl_seconds=300, now=NOW)
        args.update(kw)
        return eh.issue_authorization(**args)

    def test_issue_on_proceed(self):
        auth = self._issue()
        self.assertIsNotNone(auth)
        self.assertEqual(auth["state"], "AUTHORIZED")
        self.assertEqual(eh.load_for_run(self.root, "run-0945")["authorization_id"],
                         auth["authorization_id"])

    def test_halt_issues_no_authorization(self):
        auth = self._issue(decision=decision(verdict="halt"))
        self.assertIsNone(auth)
        self.assertIsNone(eh.load_for_run(self.root, "run-0945"))

    def test_no_key_issues_no_authorization(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(self._issue())

    def test_10_20_requires_valid_authorization(self):
        self._issue()
        ok, reason = eh.require_execution_authorization(
            root=self.root, run_id="run-0945", decision=decision(),
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=2, notional=200, risk=10, broker="alpaca",
            account_id="paper-acct", now=NOW)
        self.assertTrue(ok, reason)

    def test_10_20_blocks_when_authorization_missing(self):
        ok, reason = eh.require_execution_authorization(
            root=self.root, run_id="run-0945", decision=decision(),
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=2, notional=200, risk=10, broker="alpaca",
            account_id="paper-acct", now=NOW)
        self.assertFalse(ok)
        self.assertIn("authorization", reason.lower())

    def test_10_20_blocks_when_flag_disabled(self):
        with mock.patch.dict(os.environ, {**self.env, "EXECUTION_AUTHORIZATION_ENABLED": "false"},
                             clear=True):
            ok, reason = eh.require_execution_authorization(
                root=self.root, run_id="run-0945", decision=decision(),
                strategy_id="research-long", symbol="AAPL", side="buy",
                quantity=2, notional=200, risk=10, broker="alpaca",
                account_id="paper-acct", now=NOW)
        self.assertFalse(ok)
        self.assertIn("flag", reason.lower())

    def test_10_20_blocks_expired_authorization(self):
        self._issue(ttl_seconds=1)
        ok, reason = eh.require_execution_authorization(
            root=self.root, run_id="run-0945", decision=decision(),
            strategy_id="research-long", symbol="AAPL", side="buy",
            quantity=2, notional=200, risk=10, broker="alpaca",
            account_id="paper-acct", now=datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc))
        self.assertFalse(ok)
        self.assertIn("expired", reason.lower())

    def test_run_decision_dataclass_and_latest_subset_roundtrip(self):
        """Regression: orchestrator passes a RunDecision; 10:20 reads latest.json."""
        from dataclasses import asdict
        from scripts.desk_orchestrator import RunDecision

        decision = RunDecision(run_id="run-0945", generated_at=NOW.isoformat(),
                               verdict="proceed", reports=[], reasons=[],
                               safety={}, overrides=[])
        full = asdict(decision)
        (self.root / "outputs/orchestrator/run-0945/decision.json").write_text(
            json.dumps(full), encoding="utf-8")
        (self.root / "outputs/orchestrator/latest.json").write_text(
            json.dumps({"run_id": "run-0945", "verdict": "proceed",
                        "generated_at": NOW.isoformat()}), encoding="utf-8")
        auth = eh.issue_for_run(self.root, full)
        self.assertIsNotNone(auth)
        ok, reason = eh.require_for_run(
            self.root, {"run_id": "run-0945", "verdict": "proceed",
                        "generated_at": NOW.isoformat()})
        self.assertTrue(ok, reason)

    def test_authorization_never_calls_an_llm(self):
        src = Path(eh.__file__).read_text(encoding="utf-8")
        for banned in ("fleet_llm", "openai", "nemotron", "chat/completions"):
            self.assertNotIn(banned, src.lower())


if __name__ == "__main__":
    unittest.main()
