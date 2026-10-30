@echo off
REM Weekday 10:30 ET day-trade session (after the opening range forms):
REM   1) daytrade_scout - opening range + VWAP + zone confluence setups
REM   2) paper_auto --day-trade - LLM judges the setups through every gate
REM      (desk cross-check + regime + zones), places day-trade entries tagged
REM      for the 15:45 flatten, and posts the buy rationale to Slack.
REM Registered in Task Scheduler as AI-Desk-DayTrade-1030.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
set TA_PY=C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.venv\Scripts\python.exe
if not exist outputs mkdir outputs
echo.>>outputs\daytrade.log
echo === daytrade session %DATE% %TIME% ===>>outputs\daytrade.log
"%TA_PY%" scripts\daytrade_scout.py>>outputs\daytrade.log 2>&1
"%TA_PY%" scripts\tradingagents_paper_auto.py --day-trade --quick-model openai/gpt-oss-20b --deep-model openai/gpt-oss-20b --max-notional 2000>>outputs\daytrade.log 2>&1
echo === daytrade session done %DATE% %TIME% ===>>outputs\daytrade.log
