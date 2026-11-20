@echo off
REM Friday 16:45 ET: post the week's evidence (objective vs reality, decisions,
REM gates, scorecard) to Slack. Registered as AI-Desk-WeeklyReview-1645.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
".venv\Scripts\python.exe" scripts\weekly_review.py>>outputs\weekly-review.log 2>&1
