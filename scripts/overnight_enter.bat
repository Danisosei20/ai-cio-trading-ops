@echo off
REM Overnight pilot: enter NVDA at close when close>50d SMA (backtested edge)
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
set TA_PY=C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.venv\Scripts\python.exe
"%TA_PY%" scripts\overnight_pilot.py --enter --tickers NVDA >>outputs\overnight.log 2>&1
