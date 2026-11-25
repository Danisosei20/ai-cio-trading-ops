@echo off
REM Weekday 15:10 ET options loop (repo owner rule 2026-10-14):
REM   1. EXITS   options_guard (take +2% / cut -25% / DTE / stale)
REM   2. ENTRY   best gated screener name (one buy)
REM   3. NOTIFY  the options watch itself
REM Registered in Task Scheduler as AI-Desk-OptionsWatch-1510.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
if errorlevel 1 exit /b 2
set TA_PY=C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.venv\Scripts\python.exe
if not exist outputs mkdir outputs
echo.>>outputs\options-watch.log
echo === options watch started %DATE% %TIME% ===>>outputs\options-watch.log
"%TA_PY%" scripts\reconcile_fills.py --quiet>>outputs\options-watch.log 2>&1
set RECON_RC=%ERRORLEVEL%
"%TA_PY%" scripts\options_watch_trade.py --execute>>outputs\options-watch.log 2>&1
set WATCH_RC=%ERRORLEVEL%
echo === options watch done rc=%RECON_RC%/%WATCH_RC% %DATE% %TIME% ===>>outputs\options-watch.log
if not "%RECON_RC%"=="0" exit /b %RECON_RC%
exit /b %WATCH_RC%
