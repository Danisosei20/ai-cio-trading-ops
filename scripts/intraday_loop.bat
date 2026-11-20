@echo off
REM ============================================================
REM  Continuous intraday day-trade loop (AI-CIO desk).
REM  Replaces the old single 10:30 scout run with a loop that scans
REM  every 45 min from 10:00 to 15:30 ET, judges new setups, and
REM  flattens at 15:45. Registered as AI-Desk-IntradayLoop.
REM ============================================================
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
set TA_PY=C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.venv\Scripts\python.exe
if not exist outputs mkdir outputs
echo.>>outputs\intraday.log
echo === intraday loop started %DATE% %TIME% ===>>outputs\intraday.log
"%TA_PY%" scripts\intraday_loop.py --interval 45 --start 10:00 --end 15:30>>outputs\intraday.log 2>&1
echo === intraday loop done %DATE% %TIME% ===>>outputs\intraday.log
"%TA_PY%" scripts\flatten_daytrades.py>>outputs\intraday.log 2>&1
