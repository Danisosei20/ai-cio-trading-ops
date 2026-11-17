@echo off
REM Office viewer (the /trading dashboard) autostart.
REM Registered as AI-Desk-Office-Viewer (ONLOGON). The viewer serves the
REM site whenever the operator is at the machine; it places no orders.
REM Idempotent: if something already listens on 8766, do nothing.
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
netstat -ano | findstr /R "LISTENING.*:8766" >nul
if %errorlevel%==0 (
  echo viewer already listening on 8766
  exit /b 0
)
echo %date% %time% starting office viewer >>outputs\office.log
start "agent-office" /min .venv\Scripts\python.exe scripts\agent_office.py