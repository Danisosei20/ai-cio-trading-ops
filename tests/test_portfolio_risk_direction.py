"""Direction-aware exposure accounting: the long book and the short book are
separate, each capped on its own.

Isolation mirrors `scripts/paper_certification.py`'s `isolated_risk()`: the
controller's module-level `STATE_FILE` / `LOCK_FILE` / `KILL_SWITCH_FILE` are
redirected to a temp dir, so the real kill switch and ledger are untouched.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from robinhood_tools import portfolio_risk as pr


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


if __name__ == "__main__":
    unittest.main()
