"""Overnight strategy: enforce its own window and persist dry-run entries."""
import unittest
from datetime import time

from scripts import overnight_pilot


class OvernightHardeningTests(unittest.TestCase):
    def test_enter_window_is_15_40_to_15_55(self):
        self.assertEqual(overnight_pilot.ENTER_WINDOW, ("15:40", "15:55"))

    def test_in_enter_window(self):
        self.assertTrue(overnight_pilot.in_window(time(15, 40), overnight_pilot.ENTER_WINDOW))
        self.assertTrue(overnight_pilot.in_window(time(15, 55), overnight_pilot.ENTER_WINDOW))
        self.assertFalse(overnight_pilot.in_window(time(15, 39), overnight_pilot.ENTER_WINDOW))
        self.assertFalse(overnight_pilot.in_window(time(15, 56), overnight_pilot.ENTER_WINDOW))

    def test_entries_state_captures_dry_run_and_live(self):
        result = {"entries": [{"ticker": "AAPL", "dry_run": True},
                              {"ticker": "MSFT", "order_id": "abc"}]}
        state = overnight_pilot.entries_state(result, "2026-11-23")
        self.assertEqual(state["date"], "2026-11-23")
        self.assertEqual(len(state["entries"]), 2)
        self.assertTrue(state["entries"][0]["dry_run"])
        self.assertFalse(state["entries"][1].get("dry_run", False))

    def test_entries_state_is_exit_consumable(self):
        state = overnight_pilot.entries_state(
            {"entries": [{"ticker": "AAPL", "qty": 3}]}, "2026-11-23")
        self.assertEqual(state["entries"][0]["symbol"], "AAPL")
        self.assertNotIn("ticker", state["entries"][0])

    def test_empty_result_does_not_clobber_prior_entries(self):
        prior = {"date": "2026-11-23", "entries": [{"symbol": "AAPL", "qty": 3}]}
        state = overnight_pilot.entries_state({"entries": []}, "2026-11-23", prior)
        self.assertEqual(len(state["entries"]), 1)

    def test_duplicate_entry_not_recorded_twice(self):
        item = {"ticker": "AAPL", "qty": 3}
        state = overnight_pilot.entries_state(
            {"entries": [item]}, "2026-11-23",
            {"entries": [{"symbol": "AAPL", "qty": 3}]})
        self.assertEqual(len(state["entries"]), 1)
