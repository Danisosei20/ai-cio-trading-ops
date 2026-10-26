@echo off
REM Weekday 16:30 ET: settle the TradingAgents memory log (outcomes vs
REM benchmark + LLM reflections for future debates), then grade the shadow
REM track record (1/5/20 trading days vs S&P 500).
REM Registered in Task Scheduler as AI-Desk-ScoreShadows-1630.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
set TA_PY=C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.venv\Scripts\python.exe
"%TA_PY%" scripts\settle_memory.py>>outputs\settle-memory.log 2>&1
".venv\Scripts\python.exe" scripts\score_shadow.py>>outputs\score-shadow.log 2>&1
