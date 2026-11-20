@echo off
REM Overnight pilot: multi-stock entry at close when close > 50d SMA
REM Scans NVDA, AAPL, MSFT, AMD, META, AMZN, GOOGL, TSLA, SPY, QQQ
REM Ranks candidates by overnight score (trend, VWAP/POC confluence,
REM relative strength vs SPY, volume/liquidity, EMA alignment)
REM
REM DRY-RUN by owner standing rule: this strategy has never placed a
REM trade (the pilot's main() carried 13 unresolved references until
REM 2026-11-16 and had never completed a run). Scans and scores now;
REM placing live needs the desk owner's explicit word, not a bug fix.
REM The exit task is NOT dry-run: closing a position is never blocked.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
set TA_PY=C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.venv\Scripts\python.exe
"%TA_PY%" scripts\overnight_pilot.py --enter --dry-run >>outputs\overnight.log 2>&1