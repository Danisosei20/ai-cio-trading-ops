"""Broker-unavailable must never render as 'desk is in cash'."""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from scripts import trading_state as ts


class TradingStateAvailabilityTests(unittest.TestCase):
    def test_positions_by_direction_fails_closed_on_none(self):
        with patch.object(ts, "_positions", return_value=None):
            result = ts._positions_by_direction(None)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["long"]["count"])
        self.assertIsNone(result["total_unrealized_pl"])

    def test_state_publishes_unavailable_status(self):
        helpers = {
            "_positions": None,
            "_open_orders": None,
            "_live_regime": "UNKNOWN",
            "_objective": {},
            "_today_records": [],
            "_option_entries": [],
            "_reconciliation": {"status": "never run"},
            "_position_odds": {"status": "never run"},
            "_scorecard": {},
            "_strategy_performance": {},
            "_recent_shadows": [],
        }
        with ExitStack() as stack:
            for name, value in helpers.items():
                stack.enter_context(patch.object(ts, name, return_value=value))
            state = ts.state()
        self.assertEqual(state["positions_status"], "unavailable")
        self.assertEqual(state["open_orders_status"], "unavailable")
        self.assertEqual(state["positions"], [])
        self.assertIsNone(state["open_orders"])
