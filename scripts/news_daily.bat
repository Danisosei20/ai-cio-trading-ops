@echo off
REM Daily AI news readout -- research only, no orders.
REM Registered in Task Scheduler as AI-Desk-NewsReadout-1610, weekdays.
REM Reads keys from ../TradingAgents/.env via news_scout.py and news_explainer.py.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
if errorlevel 1 exit /b 2
if not exist outputs\news mkdir outputs\news
.venv\Scripts\python.exe scripts\news_daily.py --top 8 >>outputs\news\news_daily.log 2>&1
set RC=%ERRORLEVEL%
exit /b %RC%
