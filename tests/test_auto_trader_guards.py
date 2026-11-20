"""Regression guards for the autonomous auto-trader.

2026-11-05 live defects:
  - MAA: "portfolio risk check failed: UnboundLocalError" — `cap` was read by
    the portfolio-risk gate before it was assigned inside the ticker loop,
    which made it an unbound local for the whole function.
  - APP/CPAY/HD/TPR/ZTS: "research exceeded 600s" — the OpenAI SDK's default
    600s read timeout meant one stalled provider call consumed the entire
    per-ticker budget. The trader now forwards an explicit per-request
    `timeout` into the LLM client factory.
  - NVDA/AMD/TSLA shorts: every short setup was charged to the *long* book's
    cap, and a short entry was booked to the controller as a *release*, so
    short exposure was never counted at all. Two guards pin the split: a
    short is sized to the short book's remaining budget, and it is forwarded
    to the controller with `short=True` so it lands in `book["short"]`.
"""
from __future__ import annotations

import ast
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from scripts import daytrade_engine as de

ROOT = Path(__file__).resolve().parents[1]
AUTO = ROOT / "scripts" / "tradingagents_paper_auto.py"
FACTORY = (ROOT.parent / "TradingAgents" / "tradingagents" / "llm_clients"
           / "factory.py")


def _ticker_loop_line(tree: ast.AST) -> int:
    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and node.target.id == "ticker"):
            return node.lineno
    raise AssertionError("ticker loop not found")


def _assigned_lines(tree: ast.AST, name: str) -> list[int]:
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]


class AutoTraderGuardTests(unittest.TestCase):
    def setUp(self):
        self.source = AUTO.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.loop_line = _ticker_loop_line(self.tree)

    def test_cap_assigned_before_ticker_loop(self):
        """`cap` must exist before the loop that reads it (MAA UnboundLocalError)."""
        lines = _assigned_lines(self.tree, "cap")
        self.assertTrue(lines, "cap is never assigned")
        self.assertLess(
            min(lines), self.loop_line,
            "cap must be assigned before the ticker loop; assigning it only "
            "inside the loop makes it an unbound local at the risk gate",
        )

    def test_risk_controller_initialised_before_loop(self):
        """`_ctrl` must be bound before the loop so a failed check leaves None."""
        lines = _assigned_lines(self.tree, "_ctrl")
        self.assertTrue(lines, "_ctrl is never assigned")
        self.assertLess(min(lines), self.loop_line)

    def test_exits_bypass_new_risk_gate(self):
        """Only BUY signals call the portfolio risk gate; exits are never blocked."""
        self.assertIn('if not rec.get("reason") and sig in BUY_SIGNALS:', self.source)

    def test_llm_timeout_is_forwarded_by_factory(self):
        src = FACTORY.read_text(encoding="utf-8")
        self.assertIn('config.get("timeout")', src)
        self.assertIn('kwargs["timeout"]', src)

    def test_research_sets_explicit_llm_timeout(self):
        self.assertIn('config["timeout"] = args.llm_timeout', self.source)
        self.assertIn('config["llm_max_retries"] = args.llm_max_retries',
                      self.source)

    def test_default_models_are_not_end_of_life(self):
        """The argparse defaults must not point at a retired NVIDIA NIM model.

        2026-11-05: moonshotai/kimi-k3 (the old default) returned HTTP 410
        "reached its end of life on 2026-10-15", so every run that relied on
        the defaults failed before research could start. meta/llama-3.3-70b
        is retired as well.
        """
        eol = ("moonshotai/kimi-k3", "meta/llama-3.3-70b-instruct")
        for flag in ("--quick-model", "--deep-model"):
            node = next(
                (n for n in ast.walk(self.tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", "") == "add_argument"
                 and n.args and isinstance(n.args[0], ast.Constant)
                 and n.args[0].value == flag),
                None,
            )
            self.assertIsNotNone(node, f"{flag} is not declared")
            default = next(
                (kw.value for kw in node.keywords if kw.arg == "default"), None
            )
            self.assertIsInstance(default, ast.Constant, f"{flag} has no literal default")
            self.assertNotIn(default.value, eol,
                             f"{flag} defaults to an end-of-life model")

    def test_default_provider_is_nvidia(self):
        """No OpenAI path: the desk runs on NVIDIA NIM."""
        node = next(
            (n for n in ast.walk(self.tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "add_argument"
             and n.args and isinstance(n.args[0], ast.Constant)
             and n.args[0].value == "--provider"),
            None,
        )
        self.assertIsNotNone(node)
        default = next(kw.value for kw in node.keywords if kw.arg == "default")
        self.assertEqual(default.value, "nvidia")


class OrderTimeoutGuardTests(unittest.TestCase):
    """The order timeout must bound wall-clock time, not just the future wait.

    2026-11-05: `_place_order_with_timeout` wrapped the worker in a
    `ThreadPoolExecutor` context manager. `future.result(timeout=...)` raised on
    time, but the `with` block's `__exit__` then called `shutdown(wait=True)`
    and blocked until the worker finished anyway - a 1s timeout still took the
    full 5s of the hung call. A hung research request would have stalled the
    whole intraday cycle while reporting a timeout.
    """

    def test_timeout_returns_without_waiting_for_the_worker(self):
        import time

        from scripts import daytrade_engine as de

        saved_place, saved_log = de._place_order, de._log

        def _hang(plan):
            time.sleep(5)
            return True, "late fill", "late-id"

        try:
            de._place_order = _hang
            de._log = lambda msg: None

            started = time.monotonic()
            ok, reason, order_id = de._place_order_with_timeout(
                {"ticker": "GUARDT"}, timeout_sec=1)
            elapsed = time.monotonic() - started
        finally:
            de._place_order, de._log = saved_place, saved_log

        self.assertFalse(ok, "a timed-out placement must not be reported as filled")
        self.assertIsNone(order_id)
        self.assertIn("timeout", reason)
        self.assertIn("unconfirmed", reason,
                      "the caller must know the order may still reach the broker")
        self.assertLess(elapsed, 2.5,
                        f"timeout did not bound wall clock: {elapsed:.1f}s for a 1s bound")

    def test_completed_worker_result_is_returned(self):
        from scripts import daytrade_engine as de

        saved_place, saved_log = de._place_order, de._log
        try:
            de._place_order = lambda plan: (True, "accepted", "order-42")
            de._log = lambda msg: None
            ok, reason, order_id = de._place_order_with_timeout(
                {"ticker": "GUARDO"}, timeout_sec=5)
        finally:
            de._place_order, de._log = saved_place, saved_log

        self.assertTrue(ok)
        self.assertEqual(order_id, "order-42")
        self.assertEqual(reason, "accepted")


class ShortBudgetSizingTests(unittest.TestCase):
    """A short is sized to the short book's remaining budget.

    2026-11-05: NVDA/AMD/TSLA shorted at 2R and then died at the desk-wide
    notional cap, because the cap could not tell a short from a long and the
    size was never clamped to what the short book could still hold.
    """

    def test_short_clamps_to_remaining_budget_not_dropped(self):
        # 0.25% of 100k = 250 risk; stop 1% away => 250 shares; a $5,000
        # budget at $100 entry clamps it to 50 shares, not 0.
        qty = de._calc_position_size(Decimal("100000"), Decimal("100"),
                                     Decimal("101"), "short",
                                     remaining_notional=Decimal("5000"))
        self.assertGreater(qty, 0)
        self.assertLessEqual(qty * Decimal("100"), Decimal("5000"))

    def test_short_returns_zero_when_budget_cannot_hold_one_share(self):
        # Floor, never up: $50 cannot buy a $100 share, so the size is 0
        # rather than 1 (which would breach the very cap being clamped to).
        qty = de._calc_position_size(Decimal("100000"), Decimal("100"),
                                     Decimal("101"), "short",
                                     remaining_notional=Decimal("50"))
        self.assertEqual(qty, 0)

    def test_long_size_unchanged_when_no_remaining_given(self):
        qty = de._calc_position_size(Decimal("100000"), Decimal("100"),
                                     Decimal("99"), "long")
        self.assertGreater(qty, 0)

    def test_zero_price_short_with_a_budget_fails_closed(self):
        # Dividing the budget by a $0 entry would raise and kill the whole
        # trading cycle: a zero quote is missing evidence, not permission.
        qty = de._calc_position_size(Decimal("100000"), Decimal("0"),
                                     Decimal("101"), "short",
                                     remaining_notional=Decimal("5000"))
        self.assertEqual(qty, 0)

    def test_zero_size_reason_names_the_actual_cause(self):
        # A zero size has three causes, and blaming the budget for all of
        # them sends the operator to the ledger to chase a quote problem.
        bad_quote = de._zero_short_size_reason(Decimal("0"), Decimal("5000"))
        self.assertEqual(bad_quote, "short entry price invalid")
        self.assertNotIn("budget", bad_quote)
        self.assertEqual(
            de._zero_short_size_reason(Decimal("100"), Decimal("0")),
            "short notional budget exhausted")
        fractional = de._zero_short_size_reason(Decimal("100"),
                                                Decimal("0.001"))
        self.assertIn("cannot hold one share", fractional)
        self.assertIn("0.001", fractional)
        self.assertNotIn("exhausted", fractional)


class ShortReachesRiskControllerTests(unittest.TestCase):
    """A short must reach the controller as a short, not as a long-side call
    booked as a release. Task 3 made that distinction meaningful; without the
    forward, short exposure is never counted and the split is dead code.

    The gate itself charges nothing: it checks and *notes* the attempt, and
    the book is charged only when the order reaches the broker. Charging at
    the gate is what let a dry-run short consume real short budget for good.
    """

    def test_short_plan_is_forwarded_to_the_controller_as_a_short(self):
        from robinhood_tools import portfolio_risk as pr

        recorded = {}
        real = (pr.PortfolioRiskController.check,
                pr.PortfolioRiskController.note_order,
                pr.PortfolioRiskController.record_order)

        def fake_check(self, symbol, side, notional, strategy,
                       data_age_sec=0, current_equity=0,
                       direction=None, short=None):
            recorded["check"] = {"side": side, "short": short,
                                 "direction": direction}
            return pr.RiskVerdict(True, "approved", {})

        def fake_note(self, symbol, side, strategy, notional=0):
            recorded["note"] = {"side": side, "symbol": symbol}

        def fake_record(self, symbol, side, strategy, notional=0,
                        direction=None, *, short=None):
            recorded.setdefault("record", []).append(
                {"side": side, "short": short, "symbol": symbol})

        plan = {"ticker": "QQQ", "direction": "short", "qty": 50.0,
                "entry": 100.0}
        try:
            pr.PortfolioRiskController.check = fake_check
            pr.PortfolioRiskController.note_order = fake_note
            pr.PortfolioRiskController.record_order = fake_record
            ok, reason = de._call_risk_controller(plan, Decimal("100000"))
            self.assertTrue(ok, reason)
            self.assertTrue(recorded["check"]["short"],
                            "a short must be charged to the short book")
            self.assertEqual(recorded["note"]["side"], "sell")
            self.assertIn("QQQ", recorded["note"]["symbol"])
            # the gate notes the attempt and charges nothing...
            self.assertNotIn("record", recorded)
            # ... the fill is what charges the book
            de._record_fill(plan)
        finally:
            (pr.PortfolioRiskController.check,
             pr.PortfolioRiskController.note_order,
             pr.PortfolioRiskController.record_order) = real

        self.assertEqual(len(recorded["record"]), 1)
        self.assertIs(recorded["record"][0]["short"], True)
        self.assertEqual(recorded["record"][0]["side"], "sell")
        self.assertIn("QQQ", recorded["record"][0]["symbol"])

    def test_sell_to_close_is_still_a_long_side_call(self):
        from robinhood_tools import portfolio_risk as pr

        recorded = {}
        real = (pr.PortfolioRiskController.check,
                pr.PortfolioRiskController.note_order,
                pr.PortfolioRiskController.record_order)

        def fake_check(self, symbol, side, notional, strategy,
                       data_age_sec=0, current_equity=0,
                       direction=None, short=None):
            return pr.RiskVerdict(True, "approved", {})

        def fake_note(self, symbol, side, strategy, notional=0):
            recorded["note"] = {"short": False, "side": side}

        def fake_record(self, symbol, side, strategy, notional=0,
                        direction=None, *, short=None):
            recorded.setdefault("record", []).append(
                {"short": short, "side": side})

        try:
            pr.PortfolioRiskController.check = fake_check
            pr.PortfolioRiskController.note_order = fake_note
            pr.PortfolioRiskController.record_order = fake_record
            de._call_risk_controller(
                {"ticker": "AAPL", "direction": "sell", "qty": 10.0,
                 "entry": 100.0},
                Decimal("100000"))
        finally:
            (pr.PortfolioRiskController.check,
             pr.PortfolioRiskController.note_order,
             pr.PortfolioRiskController.record_order) = real

        self.assertNotEqual(recorded["note"]["short"], True)
        self.assertEqual(recorded["note"]["side"], "sell")
        self.assertNotIn("record", recorded)


class ShortBudgetValidateSetupTests(unittest.TestCase):
    """`_validate_setup` reads the short budget and sizes to it.

    `status()` does not reload from disk, so the budget must come from a
    freshly constructed controller. The ledger is isolated to a temp dir
    (mirroring `scripts/paper_certification.py`'s `isolated_risk()`) so the
    real kill switch and exposure ledger are never touched.
    """

    # price 100, or_high 101 => risk distance $1 => 250 raw shares at 2R.
    SHORT_REC = {
        "ticker": "TSLA",
        "setup": "short_breakdown",
        "price": 100.0,
        "or_low": 99.0,
        "or_high": 101.0,
        "vwap": 100.5,
        "zone_verdict": "short_ok",
        "odds": {"available": True, "target_first": 0.45,
                 "stop_first": 0.25},
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        patcher = patch.multiple(
            "robinhood_tools.portfolio_risk",
            STATE_FILE=d / "risk_state.json",
            LOCK_FILE=d / "risk.lock",
            KILL_SWITCH_FILE=d / "KILL_SWITCH",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _occupy_short_book(self, notional: float) -> None:
        from robinhood_tools import portfolio_risk as pr
        ctrl = pr.PortfolioRiskController()
        ctrl.record_order("OCCUPIED", "sell", "intraday", notional, short=True)

    def test_short_is_clamped_to_the_remaining_short_budget(self):
        # $4,000 of the $5,000 short book is already used: 250 raw shares at
        # $100 would be $25,000 notional, far past the cap.
        self._occupy_short_book(4000.0)
        approved, reason, plan = de._validate_setup(
            self.SHORT_REC, Decimal("100000"))
        self.assertTrue(approved, reason)
        self.assertEqual(plan["direction"], "short")
        self.assertEqual(plan["qty"], 10.0)
        self.assertLessEqual(plan["qty"] * plan["entry"], 1000.0)

    def test_long_entry_ignores_the_short_budget(self):
        self._occupy_short_book(5000.0)
        rec = dict(self.SHORT_REC, setup="long_reclaim",
                   zone_verdict="long_ok")
        approved, reason, plan = de._validate_setup(
            rec, Decimal("100000"))
        self.assertTrue(approved, reason)
        # $1 risk distance on a long at $100 => 250 raw shares, unclamped.
        self.assertEqual(plan["qty"], 250.0)

    def test_short_blocked_when_budget_cannot_hold_one_share(self):
        self._occupy_short_book(5000.0)
        approved, reason, _ = de._validate_setup(
            self.SHORT_REC, Decimal("100000"))
        self.assertFalse(approved)
        self.assertEqual(reason, "short notional budget exhausted")

    def test_zero_price_short_does_not_blame_the_budget(self):
        # The ledger is empty here, so the budget is wide open: a $0 quote
        # is missing evidence and must not be reported as a budget block.
        rec = dict(self.SHORT_REC, price=0.0)
        approved, reason, _ = de._validate_setup(
            rec, Decimal("100000"))
        self.assertFalse(approved)
        self.assertEqual(reason, "short entry price invalid")

    def test_fractional_budget_reports_the_shortfall_not_exhaustion(self):
        # $0.001 left is not an exhausted budget: it cannot hold one share,
        # and saying "exhausted" would misdescribe what actually happened.
        self._occupy_short_book(4999.999)
        approved, reason, _ = de._validate_setup(
            self.SHORT_REC, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("cannot hold one share", reason)
        self.assertNotIn("exhausted", reason)

    def test_budget_read_failure_fails_closed(self):
        # Missing evidence is not permission to trade.
        with patch.object(de, "PortfolioRiskController",
                          side_effect=RuntimeError("ledger unreadable")):
            approved, reason, _ = de._validate_setup(
                self.SHORT_REC, Decimal("100000"))
        self.assertFalse(approved)
        self.assertIn("ledger unreadable", reason)


if __name__ == "__main__":
    unittest.main()
