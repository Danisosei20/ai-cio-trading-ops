@echo off
REM Overnight pilot: exit all open overnight positions at next open
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
if errorlevel 1 exit /b 2
set TA_PY=C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.venv\Scripts\python.exe
"%TA_PY%" scripts\overnight_pilot.py --exit >>outputs\overnight.log 2>&1
set RC=%ERRORLEVEL%
exit /b %RC%