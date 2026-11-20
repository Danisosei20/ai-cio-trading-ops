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
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

from robinhood_tools.order_alerts import (
    equity_message,
    is_simulation_service,
    option_message,
    post_order_alert,
)
from robinhood_tools.sim_broker import SimulationBroker

ROOT = Path(__file__).resolve().parents[1]
OPTIONS_FLOW = (ROOT / "robinhood_tools" / "options_flow.py").read_text(
    encoding="utf-8")
PAPER_FLOW = (ROOT / "robinhood_tools" / "paper_flow.py").read_text(
    encoding="utf-8")


SENT: list[dict] = []


class RealLikeBroker(SimulationBroker):
    """Simulated fills, but deliberately NOT flagged as a simulation.

    The alert seam suppresses orders placed through SimulationBroker so that
    unit tests and the rehearsal cannot page the live Slack channel. This
    subclass keeps the outcome tests exercising the real send path with a
    fake notifier, by opting out of that suppression.
    """

    is_simulation = False


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

    def test_simulated_order_is_suppressed_and_sends_nothing(self):
        # 2026-11-19: a full test-suite run paged the live alerts channel with
        # synthetic fills. A simulated order must never reach a real notifier.
        with patch("robinhood_tools.settings.load_env", return_value={}), \
                patch("robinhood_tools.slack_web_api.SlackWebApiNotifier",
                      new=FakeNotifier):
            outcome = post_order_alert(settings=_settings(), message="sim",
                                       simulated=True)
        self.assertEqual(outcome, "suppressed")
        self.assertEqual(SENT, [])


class BackendClassificationTests(unittest.TestCase):
    """Only an explicitly-flagged venue backend may page the live channel."""

    def setUp(self):
        SENT.clear()
        FakeNotifier.construct_exc = None

    def test_real_venue_backends_are_not_simulations(self):
        from robinhood_tools.alpaca_options import AlpacaOptionsBackend
        from robinhood_tools.alpaca_paper import AlpacaPaperBackend

        self.assertIs(AlpacaPaperBackend.is_simulation, False)
        self.assertIs(AlpacaOptionsBackend.is_simulation, False)

    def test_in_memory_backends_are_simulations(self):
        from robinhood_tools.paper import PaperTradingBackend

        self.assertIs(SimulationBroker.is_simulation, True)
        self.assertIs(PaperTradingBackend.is_simulation, True)

    def test_unknown_backend_fails_closed(self):
        # A test stub with no flag is suppressed rather than paged.
        unknown = SimpleNamespace(backend=SimpleNamespace())
        no_backend = SimpleNamespace(backend=None)
        self.assertTrue(is_simulation_service(unknown))
        self.assertTrue(is_simulation_service(no_backend))

    def test_flagged_venue_service_is_allowed(self):
        venue = SimpleNamespace(backend=SimpleNamespace(is_simulation=False))
        self.assertFalse(is_simulation_service(venue))

    def test_real_venue_order_still_reaches_the_channel(self):
        # Suppression must not quietly kill the owner's per-order alert.
        from robinhood_tools.alpaca_paper import AlpacaPaperBackend

        venue = SimpleNamespace(backend=AlpacaPaperBackend(None))
        with patch("robinhood_tools.settings.load_env", return_value={}), \
                patch("robinhood_tools.slack_web_api.SlackWebApiNotifier",
                      new=FakeNotifier):
            outcome = post_order_alert(
                settings=_settings(), message="live order",
                simulated=is_simulation_service(venue))
        self.assertEqual(outcome, "sent")
        self.assertEqual(len(SENT), 1)


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


class AlertOutcomeRecordedTest(unittest.TestCase):
    """The placement path must REPORT the alert outcome, not just attempt it.

    On 2026-11-17 an order reached the broker with no alert and, because the
    seam's return value was discarded inside a bare try/except, there was no
    way to tell afterwards whether Slack had failed or the seam had never
    been reached. These two cases pin the fix at the behavioural level: the
    order always places, and the outcome always comes back.
    """

    SYM = "NVDA261120C00240000"

    def _quote(self):
        from decimal import Decimal as D

        from robinhood_tools.options import OptionContract, OptionQuote

        return OptionQuote(
            OptionContract("NVDA", "2026-11-20", D("240"), "call", self.SYM),
            D("3.80"), D("3.95"), D("237.08"),
            datetime.now(ET).isoformat(), iv=D("0.38"), delta=D("0.46"))

    def _place(self, construct_exc, broker_cls=RealLikeBroker):
        from datetime import date
        from decimal import Decimal as D

        from robinhood_tools.database import CioDatabase
        from robinhood_tools.options_flow import place_long_option
        from robinhood_tools.runtime import build_settings
        from robinhood_tools.service import RobinhoodTradingService
        from robinhood_tools.universe import Sp500Snapshot

        settings = build_settings()   # before the patch: needs real .env vars
        with tempfile.TemporaryDirectory() as tmp:
            db = CioDatabase(Path(tmp) / "cio.db")
            snap = Sp500Snapshot(frozenset({"NVDA"}),
                                 datetime.now(ET).isoformat(),
                                 "https://example.com/sp500")
            sim = broker_cls(option_quotes={self.SYM: self._quote()})
            svc = RobinhoodTradingService(
                sim, approval_store=db, sp500_snapshot=snap,
                broker_environment="paper", require_human_confirmation=False,
                execution_guard=lambda: None)
            # The seam reads env_path=".env" to build its notifier; patch only
            # that read so the notifier itself stays the fake.
            with patch("robinhood_tools.settings.load_env", return_value={}), \
                    patch("robinhood_tools.slack_web_api.SlackWebApiNotifier",
                          FakeNotifier):
                return place_long_option(
                    settings=settings, snapshot=snap, quote=self._quote(),
                    contracts=1, earnings_date=date(2026, 12, 18),
                    premium_cap=D("500"), database=db, service=svc,
                    odds_gate=False, today=date(2026, 11, 18))

    def setUp(self):
        SENT.clear()
        FakeNotifier.construct_exc = None

    def test_healthy_slack_records_sent(self):
        result = self._place(None)
        self.assertEqual(result["action"], "buy_placed")
        self.assertEqual(result["alert"], "sent")
        self.assertEqual(len(SENT), 1)

    def test_a_slack_outage_records_failed_and_still_places(self):
        FakeNotifier.construct_exc = RuntimeError("slack down")
        result = self._place(RuntimeError("slack down"))
        # The order must not be blocked by the chat layer.
        self.assertEqual(result["action"], "buy_placed")
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["alert"], "failed")
        self.assertEqual(len(SENT), 0)

    def test_simulation_broker_is_flagged_as_simulation(self):
        self.assertTrue(SimulationBroker.is_simulation)

    def test_simulated_placement_is_suppressed_and_sends_nothing(self):
        # A test/rehearsal fill must never page the live alerts channel.
        result = self._place(None, broker_cls=SimulationBroker)
        self.assertEqual(result["action"], "buy_placed")
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["alert"], "suppressed")
        self.assertEqual(SENT, [])


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

    @staticmethod
    def _assigns_alert_outcome(source: str, func_name: str) -> bool:
        """True when the function assigns the alert call's result somewhere."""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == func_name):
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Assign)
                            and isinstance(sub.value, ast.Call)
                            and isinstance(sub.value.func, ast.Name)
                            and sub.value.func.id == "post_order_alert"):
                        return True
        return False

    @staticmethod
    def _stores_alert_key(source: str, func_name: str) -> bool:
        """True when the function writes result["alert"]."""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == func_name):
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Assign)
                            and any(isinstance(t, ast.Subscript)
                                    and isinstance(t.value, ast.Name)
                                    and t.value.id == "result"
                                    and isinstance(t.slice, ast.Constant)
                                    and t.slice.value == "alert"
                                    for t in sub.targets)):
                        return True
        return False

    def test_each_placement_point_keeps_the_alert_outcome(self):
        """The outcome must be KEPT, not merely obtained.

        The original code called post_order_alert and threw the return value
        away inside a bare try/except, so a Slack failure was invisible. The
        module docstring claimed the alert 'always records an outcome' while
        nothing recorded it. On 2026-11-17 an order reached the broker with
        no alert and afterwards there was no way to tell whether Slack had
        failed or the seam had never been reached at all.

        This guard pins both halves: the call's result is assigned, and it is
        written into the result record the caller logs.
        """
        for source, func in ((OPTIONS_FLOW, "place_long_option"),
                             (OPTIONS_FLOW, "close_long_option"),
                             (PAPER_FLOW, "place_signal_order")):
            with self.subTest(path=Path(source).name, func=func):
                self.assertTrue(
                    self._assigns_alert_outcome(source, func),
                    f"{func} calls post_order_alert but discards its result")
                self.assertTrue(
                    self._stores_alert_key(source, func),
                    f"{func} does not record the outcome in result['alert']")


if __name__ == "__main__":
    unittest.main()