"""Authorization id survives 09:45 -> intraday judge -> entry order."""
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from scripts import execution_handoff, intraday_loop
from scripts import tradingagents_paper_auto as auto
from robinhood_tools import paper_flow

KEY = "chain-key"


class AuthorizationChainTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ, EXECUTION_AUTH_HMAC_KEY=KEY,
                        EXECUTION_AUTHORIZATION_ENABLED="true")
        self.patcher = mock.patch.dict(os.environ, self.env, clear=True)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_parent_id_is_readable_for_a_validated_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "outputs/paper").mkdir(parents=True)
            (root / "outputs/orchestrator/run-1").mkdir(parents=True)
            (root / "outputs/orchestrator/run-1/decision.json").write_text(
                '{"run_id":"run-1","verdict":"proceed","generated_at":"2026-11-23T09:45:00-05:00"}',
                encoding="utf-8")
            auth = execution_handoff.issue_authorization(
                root=root,
                decision={"run_id": "run-1", "verdict": "proceed",
                          "generated_at": "2026-11-23T09:45:00-05:00"},
                strategy_id="gate-parent", symbol="SPY", side="buy",
                max_quantity=10, max_notional=10000, max_risk=500,
                broker="alpaca", account_id="acct-1")
            self.assertIsNotNone(auth)
            self.assertEqual(execution_handoff.parent_id_for_run(root, "run-1"),
                             auth["authorization_id"])

    def test_judge_command_carries_authorization_id(self):
        with mock.patch.object(intraday_loop, "_log"), \
             mock.patch.object(intraday_loop.subprocess, "run") as run:
            intraday_loop.run_judge(["AAPL"], dry_run=True, authorization_id="auth-123")
        cmd = run.call_args.args[0]
        self.assertIn("--authorization-id", cmd)
        self.assertEqual(cmd[cmd.index("--authorization-id") + 1], "auth-123")

    def test_judge_command_without_id_has_no_flag(self):
        with mock.patch.object(intraday_loop, "_log"), \
             mock.patch.object(intraday_loop.subprocess, "run") as run:
            intraday_loop.run_judge(["AAPL"], dry_run=True)
        self.assertNotIn("--authorization-id", run.call_args.args[0])

    def test_auto_accepts_authorization_flag(self):
        args = auto.parse_args(["--day-trade", "--tickers", "AAPL",
                                "--authorization-id", "auth-123"])
        self.assertEqual(args.authorization_id, "auth-123")

    def test_buy_entry_order_gets_child_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            parent = self._parent(root)
            order = paper_flow.SignalOrder(symbol="AAPL", side="buy",
                                           quantity=Decimal("2"),
                                           limit_price=Decimal("100"))
            enriched = auto.authorize_entry_order(order, parent, root)
            self.assertTrue(enriched.authorization_id.startswith("child-"))
            self.assertEqual(enriched.position_effect, "open")

    def test_sell_exit_order_is_left_unauthorized(self):
        with tempfile.TemporaryDirectory() as td:
            order = paper_flow.SignalOrder(symbol="AAPL", side="sell",
                                           quantity=Decimal("2"),
                                           limit_price=Decimal("100"),
                                           position_effect="close")
            enriched = auto.authorize_entry_order(order, None, Path(td))
            self.assertIsNone(enriched.authorization_id)

    def _parent(self, root):
        from robinhood_tools import execution_authorization as ea
        (root / "outputs/paper").mkdir(parents=True, exist_ok=True)
        ev = root / "evidence.json"
        ev.write_text("{}", encoding="utf-8")
        return ea.store_authorization(ea.create_authorization(
            decision={"run_id": "run-1", "verdict": "proceed",
                      "generated_at": "2026-11-23T09:45:00-05:00"},
            root=root, artifacts=[ev], strategy_id="gate-parent", symbol="SPY",
            side="buy", max_quantity=10, max_notional=10000, max_risk=500,
            broker="alpaca", account_id="acct-1", environment="paper"), root)


if __name__ == "__main__":
    unittest.main()
