@echo off
REM Overnight pilot: exit all open overnight positions at next open
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
set TA_PY=C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.venv\Scripts\python.exe
"%TA_PY%" scripts\overnight_pilot.py --exit >>outputs\overnight.log 2>&1