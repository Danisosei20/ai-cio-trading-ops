import sys
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robinhood_tools.event_protection import EventProtection

ET = ZoneInfo("America/New_York")


class TestEventProtection(unittest.TestCase):
    def setUp(self):
        self.ep = EventProtection()
        self.now = datetime.now(ET)

    def test_earnings_block(self):
        earnings_data = {"NVDA": [(self.now + timedelta(days=1)).isoformat()]}
        result = self.ep.check_symbol("NVDA", self.now, earnings_data=earnings_data)
        self.assertEqual(result.action, "avoid")
        self.assertIn("earnings", result.reason.lower())

    def test_no_events(self):
        result = self.ep.check_symbol("AAPL", self.now, earnings_data={})
        self.assertEqual(result.action, "enter")

    def test_halted_symbol(self):
        result = self.ep.check_symbol("TSLA", self.now, halted_symbols={"TSLA"})
        self.assertEqual(result.action, "avoid")
        self.assertIn("halted", result.reason.lower())

    def test_economic_event_today(self):
        future_time = self.now + timedelta(minutes=10)
        economic_events = [
            {"event": "FOMC", "date": future_time.strftime("%Y-%m-%d"),
             "time_et": future_time.strftime("%H:%M")}
        ]
        result = self.ep.check_symbol("NVDA", self.now, economic_events=economic_events)
        self.assertEqual(result.action, "avoid")
        self.assertIn("FOMC", result.reason)


if __name__ == "__main__":
    unittest.main()