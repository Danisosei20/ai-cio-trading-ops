@echo off
REM ============================================================
REM  Continuous intraday day-trade loop (AI-CIO desk).
REM  Replaces the old single 10:30 scout run with a loop that scans
REM  every 45 min from 10:00 to 15:30 ET, judges new setups, and
REM  flattens at 15:45. Registered as AI-Desk-IntradayLoop.
REM ============================================================
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
set TA_PY=C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.venv\Scripts\python.exe
REM Every TradingAgents-dependent stage must use this exact interpreter.
set TRADINGAGENTS_VENV_PYTHON=%TA_PY%
set LOG=outputs\intraday.log
if not exist outputs mkdir outputs
"%TA_PY%" -c "import tradingagents" >nul 2>&1
if errorlevel 1 (
  echo FATAL: %TA_PY% cannot import tradingagents; research stages fail closed.>>%LOG%
  exit /b 2
)
echo.>>outputs\intraday.log
echo === intraday loop started %DATE% %TIME% ===>>outputs\intraday.log
REM Do not edit this launcher between 09:45 and 16:05 ET; cmd.exe seeks by byte offset.
"%TA_PY%" scripts\intraday_loop.py --interval 45 --start 10:00 --end 15:30>>outputs\intraday.log 2>&1
set LOOP_RC=%ERRORLEVEL%
echo === intraday loop done rc=%LOOP_RC% %DATE% %TIME% ===>>outputs\intraday.log
"%TA_PY%" scripts\flatten_daytrades.py>>outputs\intraday.log 2>&1
set CLOSE_RC=%ERRORLEVEL%
"%TA_PY%" scripts\flatten_verified.py>>outputs\intraday.log 2>&1
set FLAT_RC=%ERRORLEVEL%
if not "%CLOSE_RC%"=="0" set FLAT_RC=%CLOSE_RC%
if not "%LOOP_RC%"=="0" exit /b %LOOP_RC%
exit /b %FLAT_RC%
