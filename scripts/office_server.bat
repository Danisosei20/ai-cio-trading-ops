@echo off
REM Office viewer (the /trading dashboard) autostart.
REM Registered in HKCU Run as AI-Desk-Office-Viewer. The viewer serves the
REM site whenever the operator is at the machine; it places no orders.
REM Idempotent: if something already listens on 8766, do nothing.
REM Output is appended to outputs\office.log: the 2026-11-17 viewer died
REM mid-session with no trace because the previous version discarded it.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
netstat -ano | findstr /R "LISTENING.*:8766" >nul
if %errorlevel%==0 (
  echo %date% %time% viewer already listening on 8766 >>outputs\office.log
  exit /b 0
)
echo %date% %time% starting office viewer >>outputs\office.log
if not exist outputs mkdir outputs
start "agent-office" /min .venv\Scripts\python.exe scripts\agent_office.py >>outputs\office.log 2>&1