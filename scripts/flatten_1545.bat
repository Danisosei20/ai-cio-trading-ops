@echo off
REM Weekday 15:45 ET: flatten day-trade positions before the close.
REM Registered in Task Scheduler as AI-Desk-Flatten-1545.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
if errorlevel 1 exit /b 2
if not exist outputs mkdir outputs
REM Protective closes must run first; verification then writes a terminal manifest.
".venv\Scripts\python.exe" scripts\flatten_daytrades.py>>outputs\flatten.log 2>&1
set CLOSE_RC=%ERRORLEVEL%
".venv\Scripts\python.exe" scripts\flatten_verified.py>>outputs\flatten.log 2>&1
set FLAT_RC=%ERRORLEVEL%
if not "%CLOSE_RC%"=="0" exit /b %CLOSE_RC%
exit /b %FLAT_RC%
