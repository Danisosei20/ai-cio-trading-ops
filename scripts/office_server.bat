@echo off
REM Office viewer (the /trading dashboard) autostart.
REM Registered in HKCU Run as AI-Desk-Office-Viewer. The viewer serves the
REM site whenever the operator is at the machine; it places no orders.
REM
REM Idempotent on TWO checks, because 2026-11-17 produced two servers:
REM Windows SO_REUSEADDR let a second instance bind 8766 alongside the
REM first, and requests hit either one - so the dashboard served STALE
REM code (the odds panel kept quoting a position that had already closed)
REM and the operator could not tell which instance answered.
REM   1. an agent_office python process already exists -> stop
REM   2. something already listens on 8766 -> stop
REM Output is appended to outputs\office.log so a crash leaves a trace.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
if errorlevel 1 exit /b 2
if not exist outputs mkdir outputs
tasklist /fi "imagename eq python.exe" /fo csv 2>nul | findstr /i "agent_office" >nul
if %errorlevel%==0 (
  echo %date% %time% viewer already running - not starting a second >>outputs\office.log
  exit /b 0
)
netstat -ano | findstr /R "LISTENING.*:8766" >nul
if %errorlevel%==0 (
  echo %date% %time% viewer already listening on 8766 >>outputs\office.log
  exit /b 0
)
echo %date% %time% starting office viewer >>outputs\office.log
start "agent-office" /min .venv\Scripts\python.exe scripts\agent_office.py >>outputs\office.log 2>&1