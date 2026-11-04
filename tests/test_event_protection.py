import sys
sys.path.insert(0, '.')
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from robinhood_tools.event_protection import EventProtection

ET = ZoneInfo("America/New_York")
ep = EventProtection()

# Test earnings block
now = datetime.now(ET)
earnings_data = {"NVDA": [(now + timedelta(days=1)).isoformat()]}
result = ep.check_symbol("NVDA", now, earnings_data=earnings_data)
print(f"Earnings tomorrow: action={result.action}, reason={result.reason}")
assert result.action == "avoid"
assert "earnings" in result.reason.lower()

# Test no events - should enter
result = ep.check_symbol("AAPL", now, earnings_data={})
print(f"No events: action={result.action}, reason={result.reason}")
assert result.action == "enter"

# Test halted symbol
result = ep.check_symbol("TSLA", now, halted_symbols={"TSLA"})
print(f"Halted symbol: action={result.action}, reason={result.reason}")
assert result.action == "avoid"

# Test economic event today
economic_events = [
    {"event": "FOMC", "date": now.strftime("%Y-%m-%d"), "time_et": "14:00"}
]
result = ep.check_symbol("NVDA", now, economic_events=economic_events)
print(f"Economic event today: action={result.action}, reason={result.reason}")
assert result.action == "avoid"
assert "FOMC" in result.reason

print()
print("All event protection tests passed!")
