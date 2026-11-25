@echo off
REM Weekday 09:50 ET watchdog: Slacks (once/day) if the 09:45 auto-trade run
REM missed or stalled. Registered in Task Scheduler as AI-Desk-Watchdog-0950.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
if errorlevel 1 exit /b 2
if not exist outputs mkdir outputs
".venv\Scripts\python.exe" scripts\watchdog_windows.py>>outputs\watchdog.log 2>&1
set RC=%ERRORLEVEL%
exit /b %RC%
