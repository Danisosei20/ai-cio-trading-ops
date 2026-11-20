"""Direction-aware exposure accounting: the long book and the short book are
separate, each capped on its own.

Isolation mirrors `scripts/paper_certification.py`'s `isolated_risk()`: the
controller's module-level `STATE_FILE` / `LOCK_FILE` / `KILL_SWITCH_FILE` are
redirected to a temp dir, so the real kill switch and ledger are untouched.

Both CLI gates are covered: `robinhood_tools.portfolio_risk.main()` and the
second, independent gate `scripts/portfolio_risk_check.py`, which must accept
the same direction flags or it books a short entry as a release.
"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from robinhood_tools import portfolio_risk as pr

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "portfolio_risk_check.py"
_spec = importlib.util.spec_from_file_location("portfolio_risk_check", SCRIPT_PATH)
assert _spec and _spec.loader
script_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script_check)


class _Isolated(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        p = patch.multiple(pr, STATE_FILE=d / "s.json",
                           LOCK_FILE=d / "l.lock",
                           KILL_SWITCH_FILE=d / "K")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def ctrl(self):
        return pr.PortfolioRiskController()


class DirectionAccountingTests(_Isolated):
    def test_long_sell_on_held_symbol_releases_it(self):
        c = self.ctrl()
        c.record_order("AAPL", "buy", "intraday", 4000)
        c.record_order("AAPL", "sell", "intraday", 4000)
        self.assertNotIn("AAPL", c.state.book["long"])
        self.assertNotIn("AAPL", c.state.open_positions)
        self.assertNotIn("long:AAPL", c.state.open_positions)

    def test_short_entry_opens_short_book(self):
        c = self.ctrl()
        c.record_order("QQQ", "sell", "intraday", 3000, short=True)
        self.assertIn("QQQ", c.state.book["short"])

    def test_short_entry_does_not_open_a_long(self):
        c = self.ctrl()
        c.record_order("QQQ", "sell", "intraday", 3000, short=True)
        self.assertNotIn("QQQ", c.state.book["long"])

    def test_short_exit_releases_short_book(self):
        c = self.ctrl()
        c.record_order("QQQ", "sell", "intraday", 3000, short=True)
        c.record_order("QQQ", "buy", "intraday", 3000, short=True)
        self.assertNotIn("QQQ", c.state.book["short"])
        self.assertNotIn("QQQ", c.state.open_positions)
        self.assertNotIn("short:QQQ", c.state.open_positions)

    def test_short_approved_while_long_book_is_full(self):
        # one $5,000 long fills the long book without tripping the 5-position
        # limit, so the only thing that can reject the short is a shared cap
        c = self.ctrl()
        c.record_order("SPY", "buy", "intraday", 5000)
        v = c.check("QQQ", "sell", 4000, "intraday", short=True)
        self.assertTrue(v.approved, v.reason)

    def test_short_over_cap_is_blocked(self):
        c = self.ctrl()
        c.record_order("QQQ", "sell", "intraday", 5000, short=True)
        v = c.check("TSLA", "sell", 1, "intraday", short=True)
        self.assertFalse(v.approved)
        self.assertIn("exposure", v.reason)

    def test_long_over_cap_still_blocked(self):
        # regression: the long path must keep today's rejection
        c = self.ctrl()
        c.record_order("AAPL", "buy", "intraday", 5000)
        v = c.check("MSFT", "buy", 1, "intraday")
        self.assertFalse(v.approved)
        self.assertIn("exposure", v.reason)

    def test_legacy_state_without_book_loads(self):
        legacy = {"open_positions": {"AAPL": {"strategy": "intraday",
                                             "notional": 1000,
                                             "entered_at": "x"}}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps(legacy), encoding="utf-8")
            with patch.object(pr, "STATE_FILE", p), \
                 patch.object(pr, "LOCK_FILE", Path(d) / "l.lock"), \
                 patch.object(pr, "KILL_SWITCH_FILE", Path(d) / "K"):
                c = pr.PortfolioRiskController()
        self.assertIn("AAPL", c.state.book["long"])
        v = c.check("QQQ", "sell", 4000, "intraday", short=True)
        self.assertTrue(v.approved, v.reason)


class BookSeparationTests(_Isolated):
    """The one hazard: a long `sell` that releases a held position must never
    land in the short book, and a short `sell` on an unheld symbol must never
    land in the long one."""

    def test_long_sell_of_unheld_symbol_does_not_open_a_short(self):
        c = self.ctrl()
        c.record_order("AAPL", "buy", "intraday", 4000)
        c.record_order("MSFT", "sell", "intraday", 4000)
        self.assertEqual(sorted(c.state.book["short"]), [])
        self.assertIn("AAPL", c.state.book["long"])

    def test_short_sell_does_not_release_the_long_book(self):
        c = self.ctrl()
        c.record_order("AAPL", "buy", "intraday", 4000)
        c.record_order("QQQ", "sell", "intraday", 3000, short=True)
        self.assertIn("AAPL", c.state.book["long"])
        self.assertIn("QQQ", c.state.book["short"])

    def test_short_entry_does_not_unbook_a_long_in_the_same_symbol(self):
        # The one place the branch failed open: `open_positions` was keyed by
        # symbol alone, so this short entry overwrote the long row, the long
        # book silently read zero, and the pre-existing long exposure stopped
        # being bounded by its own cap. A long and a short in one symbol is
        # legal at Alpaca, so the books must hold two rows, not one relabelled
        # row.
        c = self.ctrl()
        c.record_order("AAPL", "buy", "intraday", 4000)
        c.record_order("AAPL", "sell", "intraday", 2000, short=True)
        self.assertIn("AAPL", c.state.book["long"])
        self.assertIn("AAPL", c.state.book["short"])
        expo = c.status()["exposure"]
        self.assertEqual(expo["long_notional"], 4000)
        self.assertEqual(expo["short_notional"], 2000)
        self.assertEqual(expo["total_notional"], 6000)
        # the surviving long is still charged to the long book's cap
        # (check_exposure, not check: the entry above is inside the cooldown)
        ok, reason = c.check_exposure("AAPL", Decimal("1001"), short=False)
        self.assertFalse(ok)
        self.assertIn("exposure", reason)

    def test_long_entry_does_not_unbook_a_short_in_the_same_symbol(self):
        c = self.ctrl()
        c.record_order("AAPL", "sell", "intraday", 2000, short=True)
        c.record_order("AAPL", "buy", "intraday", 3000)
        self.assertIn("AAPL", c.state.book["short"])
        self.assertIn("AAPL", c.state.book["long"])
        self.assertEqual(c.status()["exposure"]["short_notional"], 2000)

    def test_short_release_does_not_unbook_a_long_in_the_same_symbol(self):
        c = self.ctrl()
        c.record_order("AAPL", "buy", "intraday", 4000)
        c.record_order("AAPL", "sell", "intraday", 2000, short=True)
        c.record_order("AAPL", "buy", "intraday", 2000, short=True)
        self.assertIn("AAPL", c.state.book["long"])
        self.assertNotIn("AAPL", c.state.book["short"])
        self.assertEqual(c.status()["exposure"]["long_notional"], 4000)

    def test_legacy_row_is_rekeyed_not_double_counted(self):
        # A pre-split ledger keys its rows by the bare symbol. A new record on
        # the same symbol must re-key that row, not add a second one, or the
        # notional is counted twice and the cap blocks a trade it should admit.
        legacy = {"open_positions": {"AAPL": {"strategy": "intraday",
                                             "notional": 1000,
                                             "entered_at": "x"}}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps(legacy), encoding="utf-8")
            with patch.object(pr, "STATE_FILE", p), \
                 patch.object(pr, "LOCK_FILE", Path(d) / "l.lock"), \
                 patch.object(pr, "KILL_SWITCH_FILE", Path(d) / "K"):
                c = pr.PortfolioRiskController()
                c.record_order("AAPL", "buy", "intraday", 1000)
        self.assertEqual(sorted(c.state.open_positions), ["long:AAPL"])
        self.assertEqual(c.status()["exposure"]["long_notional"], 1000)

    def test_position_count_spans_both_books(self):
        # the 5-slot cap is shared, so it must count both books' symbols
        c = self.ctrl()
        for i in range(3):
            c.record_order(f"L{i}", "buy", "intraday", 100)
            c.record_order(f"S{i}", "sell", "intraday", 100, short=True)
        self.assertEqual(len(c.state.open_positions), 6)
        v = c.check("NEW", "sell", 100, "intraday", short=True)
        self.assertFalse(v.approved)
        self.assertIn("max open positions", v.reason)
        # a repeat on a held symbol is not a new slot (buy, so the duplicate
        # cooldown from the entry above does not mask the count check)
        v2 = c.check("S0", "buy", 100, "intraday", short=True)
        self.assertTrue(v2.approved, v2.reason)

    def test_status_reports_both_books(self):
        c = self.ctrl()
        c.record_order("AAPL", "buy", "intraday", 3000)
        c.record_order("QQQ", "sell", "intraday", 2000, short=True)
        expo = c.status()["exposure"]
        self.assertEqual(expo["long_notional"], 3000)
        self.assertEqual(expo["short_notional"], 2000)
        self.assertEqual(expo["max_per_book"], 5000)
        self.assertEqual(expo["total_notional"], 5000)

    def test_direction_string_resolves_like_the_short_flag(self):
        c = self.ctrl()
        c.record_order("QQQ", "sell", "intraday", 3000, direction="short")
        self.assertIn("QQQ", c.state.book["short"])
        v = c.check("TSLA", "sell", 5001, "intraday", direction="short")
        self.assertFalse(v.approved)
        self.assertIn("exposure", v.reason)

    def test_book_survives_a_reload_from_disk(self):
        c = self.ctrl()
        c.record_order("QQQ", "sell", "intraday", 3000, short=True)
        reloaded = self.ctrl()
        self.assertIn("QQQ", reloaded.state.book["short"])
        v = reloaded.check("TSLA", "sell", 4000, "intraday", short=True)
        self.assertFalse(v.approved)
        self.assertIn("exposure", v.reason)


class NoteOrderTests(_Isolated):
    """An attempt can be recorded for the cooldown without charging a book.

    That split is what lets dry-run keep its duplicate cooldown (it reads
    `recent_orders`) while never consuming notional for a position it never
    opened.
    """

    def test_note_order_cools_down_without_charging_exposure(self):
        c = self.ctrl()
        c.note_order("QQQ", "sell", "intraday", 3000)
        self.assertEqual(c.state.book["long"], {})
        self.assertEqual(c.state.book["short"], {})
        self.assertEqual(c.status()["exposure"]["total_notional"], 0)
        v = c.check("QQQ", "sell", 100, "intraday", short=True)
        self.assertFalse(v.approved)
        self.assertIn("duplicate", v.reason)

    def test_record_order_still_cools_down(self):
        # a real fill is both a cooldown entry and a charge
        c = self.ctrl()
        c.record_order("QQQ", "sell", "intraday", 3000, short=True)
        v = c.check("QQQ", "sell", 100, "intraday", short=True)
        self.assertFalse(v.approved)
        self.assertIn("duplicate", v.reason)


class CliDirectionTests(_Isolated):
    """CLI gate flags (ruling 2): without them a short placed through `main()`
    records as a release and silently zeroes the short book."""

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = pr.main(list(argv))
        return code, json.loads(buf.getvalue().strip().splitlines()[0])

    def test_short_flag_records_a_short_entry(self):
        code, payload = self.run_cli("--symbol", "QQQ", "--side", "sell",
                                     "--notional", "3000", "--strategy",
                                     "intraday", "--short")
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["approved"], payload)
        self.assertIn("QQQ", self.ctrl().state.book["short"])

    def test_direction_short_records_a_short_entry(self):
        code, payload = self.run_cli("--symbol", "QQQ", "--side", "sell",
                                     "--notional", "3000", "--strategy",
                                     "intraday", "--direction", "short")
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["approved"], payload)
        self.assertIn("QQQ", self.ctrl().state.book["short"])

    def test_long_flag_is_the_default_behaviour(self):
        code, payload = self.run_cli("--symbol", "AAPL", "--side", "buy",
                                     "--notional", "1000", "--strategy",
                                     "intraday")
        self.assertEqual(code, 0, payload)
        state = self.ctrl().state
        self.assertIn("AAPL", state.book["long"])
        self.assertNotIn("AAPL", state.book["short"])


class ScriptGateDirectionTests(_Isolated):
    """`scripts/portfolio_risk_check.py` is a second, independent gate: its own
    `main()` calls `check()`/`record_order()`. Without the direction flags a
    short placed through it recorded as a *release*, so the short book silently
    read zero while the position was live."""

    def run_script(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = script_check.main(list(argv))
        return code, buf.getvalue()

    def test_short_flag_records_a_short_entry(self):
        code, out = self.run_script("--symbol", "QQQ", "--side", "sell",
                                    "--notional", "3000", "--strategy",
                                    "intraday", "--short")
        payload = json.loads(out.strip().splitlines()[0])
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["approved"], payload)
        state = self.ctrl().state
        self.assertIn("QQQ", state.book["short"])
        self.assertNotIn("QQQ", state.book["long"])
        self.assertEqual(state.open_positions["short:QQQ"]["direction"], "short")

    def test_direction_short_records_a_short_entry(self):
        code, out = self.run_script("--symbol", "QQQ", "--side", "sell",
                                    "--notional", "3000", "--strategy",
                                    "intraday", "--direction", "short")
        payload = json.loads(out.strip().splitlines()[0])
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["approved"], payload)
        self.assertIn("QQQ", self.ctrl().state.book["short"])

    def test_short_entry_is_charged_to_the_short_book_cap(self):
        # real accounting, not just a book key: the entry consumes the short
        # book's own budget and then blocks the next short
        code, out = self.run_script("--symbol", "QQQ", "--side", "sell",
                                    "--notional", "5000", "--strategy",
                                    "intraday", "--short")
        self.assertEqual(code, 0, json.loads(out.strip().splitlines()[0]))
        blocked = self.ctrl().check("TSLA", "sell", 1, "intraday", short=True)
        self.assertFalse(blocked.approved)
        self.assertIn("short exposure", blocked.reason)
        # ... and the long book is untouched by that short entry
        allowed = self.ctrl().check("NVDA", "buy", 4000, "intraday")
        self.assertTrue(allowed.approved, allowed.reason)

    def test_no_direction_keeps_long_only_behaviour(self):
        code, out = self.run_script("--symbol", "AAPL", "--side", "buy",
                                    "--notional", "1000", "--strategy",
                                    "intraday")
        payload = json.loads(out.strip().splitlines()[0])
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["approved"], payload)
        state = self.ctrl().state
        self.assertIn("AAPL", state.book["long"])
        self.assertNotIn("AAPL", state.book["short"])

    def test_short_exit_releases_the_short_book(self):
        self.run_script("--symbol", "QQQ", "--side", "sell",
                        "--notional", "3000", "--strategy", "intraday",
                        "--short")
        # the gate charges the notional against the book before it records, so
        # the exit is sized to fit the cap (unchanged controller behaviour; a
        # long release is charged the same way)
        code, out = self.run_script("--symbol", "QQQ", "--side", "buy",
                                    "--notional", "1000", "--strategy",
                                    "intraday", "--short")
        self.assertEqual(code, 0, json.loads(out.strip().splitlines()[0]))
        state = self.ctrl().state
        self.assertNotIn("QQQ", state.book["short"])
        self.assertNotIn("QQQ", state.open_positions)

    def test_status_still_prints_both_books(self):
        self.run_script("--symbol", "AAPL", "--side", "buy",
                        "--notional", "3000", "--strategy", "intraday")
        self.run_script("--symbol", "QQQ", "--side", "sell",
                        "--notional", "2000", "--strategy", "intraday",
                        "--short")
        code, out = self.run_script("--status")
        self.assertEqual(code, 0)
        status = json.loads(out)
        self.assertEqual(status["exposure"]["long_notional"], 3000)
        self.assertEqual(status["exposure"]["short_notional"], 2000)
        self.assertEqual(status["exposure"]["max_per_book"], 5000)


if __name__ == "__main__":
    unittest.main()
