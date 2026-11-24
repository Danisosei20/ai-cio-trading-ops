"""One deterministic entry-risk interface: unified kill switch and drawdowns."""
import tempfile
import unittest
from pathlib import Path

from robinhood_tools import entry_risk as er


class EntryRiskTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def test_no_kill_switch_by_default(self):
        self.assertFalse(er.file_kill_switch(self.root))
        self.assertFalse(er.kill_switch_engaged(self.root, db_kill=False))

    def test_file_kill_switch_blocks_entries(self):
        f = self.root / "outputs" / "paper" / "KILL_SWITCH"
        f.parent.mkdir(parents=True)
        f.write_text("", encoding="utf-8")
        self.assertTrue(er.file_kill_switch(self.root))
        self.assertTrue(er.kill_switch_engaged(self.root, db_kill=False))

    def test_db_kill_switch_also_blocks(self):
        self.assertTrue(er.kill_switch_engaged(self.root, db_kill=True))

    def test_exempt_exits_are_not_blocked_by_kill_switch(self):
        ok, reason = er.entry_permitted(
            self.root, is_exit=True, db_kill=True, current_equity=1000,
            day_start_equity=1000, week_start_equity=1000, peak_equity=1000)
        self.assertTrue(ok, reason)

    def test_entry_blocked_by_kill_switch(self):
        ok, reason = er.entry_permitted(
            self.root, is_exit=False, db_kill=True, current_equity=1000,
            day_start_equity=1000, week_start_equity=1000, peak_equity=1000)
        self.assertFalse(ok)
        self.assertIn("kill", reason.lower())

    def test_daily_drawdown_breach_blocks_entry(self):
        ok, reason = er.entry_permitted(
            self.root, is_exit=False, db_kill=False, current_equity=950,
            day_start_equity=1000, week_start_equity=1000, peak_equity=1100)
        self.assertFalse(ok)
        self.assertIn("daily", reason.lower())

    def test_weekly_drawdown_breach_blocks_entry(self):
        ok, reason = er.entry_permitted(
            self.root, is_exit=False, db_kill=False, current_equity=920,
            day_start_equity=920, week_start_equity=1000, peak_equity=1000)
        self.assertFalse(ok)
        self.assertIn("weekly", reason.lower())

    def test_healthy_equity_permits_entry(self):
        ok, reason = er.entry_permitted(
            self.root, is_exit=False, db_kill=False, current_equity=1000,
            day_start_equity=1000, week_start_equity=1000, peak_equity=1050)
        self.assertTrue(ok, reason)

    def test_missing_equity_fails_closed(self):
        ok, reason = er.entry_permitted(
            self.root, is_exit=False, db_kill=False, current_equity=0,
            day_start_equity=0, week_start_equity=0, peak_equity=0)
        self.assertFalse(ok)
        self.assertIn("equity", reason.lower())

    def test_drawdown_calculations_distinguish_baselines(self):
        d = er.drawdowns(current_equity=900, day_start_equity=1000,
                         week_start_equity=950, peak_equity=1200)
        self.assertAlmostEqual(d["daily_pct"], 0.10)
        self.assertAlmostEqual(d["weekly_pct"], 1 - 900 / 950)
        self.assertAlmostEqual(d["alltime_pct"], 0.25)

    def test_weekly_limit_constant_is_used(self):
        self.assertGreater(er.WEEKLY_DRAWDOWN_LIMIT, er.DAILY_DRAWDOWN_LIMIT)


if __name__ == "__main__":
    unittest.main()
