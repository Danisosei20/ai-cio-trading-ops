@echo off
REM Weekday 16:30 ET: grade the shadow track record (1/5/20 trading days vs
REM S&P 500). Registered in Task Scheduler as AI-Desk-ScoreShadows-1630.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
".venv\Scripts\python.exe" scripts\score_shadow.py>>outputs\score-shadow.log 2>&1
