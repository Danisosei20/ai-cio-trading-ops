@echo off
REM Overnight pilot: multi-stock entry at close when close > 50d SMA
REM Scans NVDA, AAPL, MSFT, AMD, META, AMZN, GOOGL, TSLA, SPY, QQQ
REM Ranks candidates by overnight score (trend, VWAP/POC confluence,
REM relative strength vs SPY, volume/liquidity, EMA alignment)
REM Enters qualifying setups via the desk's paper-trading guard
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
set TA_PY=C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.venv\Scripts\python.exe
"%TA_PY%" scripts\overnight_pilot.py --enter >>outputs\overnight.log 2>&1