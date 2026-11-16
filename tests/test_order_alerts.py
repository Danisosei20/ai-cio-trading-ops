"""One Slack message per order the desk places (owner directive 2026-11-16).

The gap this closes: options entries, the options guard's exits, equity
signal orders and the 15:45 flatten all placed orders with no Slack
message. On 2026-11-16 the watch logged 13 entries and the operator only
saw the ones the end-of-run summary happened to mention. The alert is
best-effort by design — a Slack outage must never break an order — so the
primary behaviour under test is "never raises, always records an outcome".
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from robinhood_tools.order_alerts import (
    equity_message,
    option_message,
    post_order_alert,
)

ROOT = Path(__file__).resolve().parents[1]
OPTIONS_FLOW = (ROOT / "robinhood_tools" / "options_flow.py").read_text(
    encoding="utf-8")
PAPER_FLOW = (ROOT / "robinhood_tools" / "paper_flow.py").read_text(
    encoding="utf-8")


SENT: list[dict] = []


class FakeNotifier:
    """Stand-in for SlackWebApiNotifier: records sends, can raise.

    Carries a real `from_values` classmethod because that is how the alert
    seam constructs it — patching with a bare function/mock silently breaks
    the attribute lookup and turns every send into a silent "failed".
    """

    construct_exc: Exception | None = None

    @classmethod
    def from_values(cls, values=None, **kw):
        return cls(exc=cls.construct_exc)

    def __init__(self, *, exc: Exception | None = None):
        self.exc = exc

    def send_approval(self, *, channel_id: str, message: str) -> dict:
        if self.exc is not None:
            raise self.exc
        SENT.append({"channel_id": channel_id, "message": message})
        return {"message_ts": "123.456", "message_link": "https://slack/x"}


def _settings():
    return SimpleNamespace(channel_id="C0123456789")


class PostOrderAlertTests(unittest.TestCase):
    def setUp(self):
        SENT.clear()
        FakeNotifier.construct_exc = None

    def tearDown(self):
        FakeNotifier.construct_exc = None

    def _post(self, message: str = "hello") -> str:
        with patch("robinhood_tools.settings.load_env", return_value={}), \
                patch("robinhood_tools.slack_web_api.SlackWebApiNotifier",
                      new=FakeNotifier):
            return post_order_alert(settings=_settings(), message=message)

    def test_sends_to_the_configured_channel(self):
        outcome = self._post("*AI CIO — PAPER ORDER PLACED* NVDA")
        self.assertEqual(outcome, "sent")
        self.assertEqual(SENT[0]["channel_id"], "C0123456789")
        self.assertIn("NVDA", SENT[0]["message"])

    def test_a_slack_outage_never_raises(self):
        for exc in (RuntimeError("Slack down"), OSError("no network"),
                    ValueError("bad token")):
            with self.subTest(exc=type(exc).__name__):
                FakeNotifier.construct_exc = exc
                # No exception escapes: the order already happened.
                self.assertEqual(self._post(), "failed")
                self.assertEqual(SENT, [])

    def test_missing_channel_config_fails_soft(self):
        # from_values raising (no bot token / no channel) is the desk's
        # real-world failure mode when .env is incomplete.
        def _raise(**kw):
            raise ValueError("SLACK_BOT_TOKEN is required for paper trade "
                             "summaries.")

        with patch("robinhood_tools.settings.load_env", return_value={}), \
                patch("robinhood_tools.slack_web_api.SlackWebApiNotifier",
                      new=_raise):
            self.assertEqual(
                post_order_alert(settings=_settings(), message="x"), "failed")


class MessageFormatTests(unittest.TestCase):
    def test_option_buy_message(self):
        text = option_message(side="buy", contract="FISV261218C00065000",
                              qty=5, limit="1.95", premium="975.00",
                              order_id="ord-1", approval_id="appr-1")
        self.assertIn("FISV261218C00065000", text)
        self.assertIn("BUY x5 @ 1.95", text)
        self.assertIn("$975.00", text)
        self.assertIn("ord-1", text)
        self.assertIn("appr-1", text)

    def test_option_sell_message_is_an_exit(self):
        text = option_message(side="sell", contract="TER261120P00220000",
                              qty=1, limit="14.8", reason="TARGET +16% >= +2%")
        self.assertIn("SELL-TO-CLOSE", text)
        self.assertIn("exit", text)
        self.assertIn("TARGET +16% >= +2%", text)

    def test_equity_messages_both_sides(self):
        buy = equity_message(side="buy", symbol="nvda", qty=10, limit="180.00",
                             stop="168.00", target="216.00")
        sell = equity_message(side="sell", symbol="NVDA", qty=10, limit="185.00")
        self.assertIn("*NVDA* — BUY x10", buy)
        self.assertIn("Bracket stop: 168.00", buy)
        self.assertIn("Take profit: 216.00", buy)
        self.assertIn("*NVDA* — SELL x10", sell)

    def test_messages_state_paper_only(self):
        self.assertIn("paper only", option_message(
            side="buy", contract="X", qty=1, limit="1.00"))
        self.assertIn("paper only", equity_message(
            side="buy", symbol="X", qty=1, limit="1.00"))


class PlacementPointGuardTests(unittest.TestCase):
    """Every placement point must call the alert seam (source guards)."""

    @staticmethod
    def _calls_in(source: str, func_name: str) -> list[str]:
        tree = ast.parse(source)
        out = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef) and node.name == func_name):
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Name)):
                        out.append(sub.func.id)
        return out

    def test_option_entry_alerts(self):
        self.assertIn("post_order_alert",
                      self._calls_in(OPTIONS_FLOW, "place_long_option"))

    def test_option_exit_alerts(self):
        self.assertIn("post_order_alert",
                      self._calls_in(OPTIONS_FLOW, "close_long_option"))

    def test_equity_order_alerts(self):
        self.assertIn("post_order_alert",
                      self._calls_in(PAPER_FLOW, "place_signal_order"))


if __name__ == "__main__":
    unittest.main()