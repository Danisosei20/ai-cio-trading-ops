"""Run-summary scoping: today's entries, not the whole log.

2026-11-16: the 10:23 Slack summary reported "options entries: 13" while
Alpaca's order history shows exactly one buy that day (RDDT put x2 at
15:10 ET). The old parser appended every `[entry] PLACED` line ever
written to outputs/options-watch.log, so last week's entries kept being
counted as today's. The count must follow the dated run banners.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def load_module():
    spec = importlib.util.spec_from_file_location(
        "run_summary", Path("scripts/run_summary.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RS = load_module()
_entries_from_watch_log = RS._entries_from_watch_log

LOG = """=== options watch started Fri 11/13/2026 15:10:00.78 ===
[entry] screen 2026-11-13 held=[] pending=[] regime=RISK_OFF
[entry] PLACED FISV call | ranked feed side=call mom20=+8.0%
[entry] PLACED TTWO call | ranked feed side=call mom20=+11.0%
=== options watch done Fri 11/13/2026 15:10:21.06 ===
=== options watch started Mon 11/16/2026 15:10:00.78 ===
[entry] screen 2026-11-16 held=[] pending=[] regime=RISK_OFF
[entry] skip SNDK put -> SNDK spot 815.38 {"ticker": "SNDK", "action": "skip"}
[entry] PLACED RDDT put | weak feed side=put mom20=-38.3% rsi=33
=== options watch done Mon 11/16/2026 15:10:21.06 ===
"""


class WatchLogScopeTests(unittest.TestCase):
    def test_today_counts_only_todays_placed_lines(self):
        entries = _entries_from_watch_log(LOG, "2026-11-16")
        self.assertEqual(len(entries), 1)
        self.assertIn("RDDT put", entries[0])

    def test_an_earlier_day_counts_only_its_own(self):
        entries = _entries_from_watch_log(LOG, "2026-11-13")
        self.assertEqual(len(entries), 2)

    def test_a_day_with_no_run_reports_an_honest_zero(self):
        # Tue 11/17 wrote nothing: zero, not last week's two.
        self.assertEqual(_entries_from_watch_log(LOG, "2026-11-17"), [])

    def test_skips_are_never_counted_as_entries(self):
        entries = _entries_from_watch_log(LOG, "2026-11-16")
        self.assertFalse(any("skip" in e for e in entries))

    def test_empty_log_is_zero(self):
        self.assertEqual(_entries_from_watch_log("", "2026-11-16"), [])


if __name__ == "__main__":
    unittest.main()