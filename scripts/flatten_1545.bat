@echo off
REM Weekday 15:45 ET: flatten day-trade positions before the close.
REM Registered in Task Scheduler as AI-Desk-Flatten-1545.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
if not exist outputs mkdir outputs
".venv\Scripts\python.exe" scripts\flatten_daytrades.py>>outputs\flatten.log 2>&1
