"""Overnight pilot tests (pure signal + session wiring; market ops need session)."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def load_module():
    spec = importlib.util.spec_from_file_location(
        "overnight_pilot", Path("scripts/overnight_pilot.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PILOT = load_module()


class OvernightSignalTest(unittest.TestCase):
    def test_uptrend_true(self):
        closes = [100.0 + i * 0.5 for i in range(80)]
        self.assertTrue(PILOT.uptrend_ok(closes, 50))

    def test_downtrend_false(self):
        closes = [200.0 - i * 0.5 for i in range(80)]
        self.assertFalse(PILOT.uptrend_ok(closes, 50))

    def test_short_history_false(self):
        self.assertFalse(PILOT.uptrend_ok([100.0] * 10, 50))

    def test_windows_documented(self):
        self.assertEqual(PILOT.ENTER_WINDOW, ("15:30", "15:55"))
        self.assertEqual(PILOT.EXIT_WINDOW, ("09:35", "10:00"))

    def test_custom_session_builder_exists(self):
        from robinhood_tools.runtime import build_paper_service_with_session

        self.assertTrue(callable(build_paper_service_with_session))


if __name__ == "__main__":
    unittest.main()
