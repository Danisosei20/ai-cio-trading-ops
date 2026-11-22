@echo off
REM ============================================================
REM  Weekday 09:45 ET autonomous paper run (AI-CIO desk).
REM  Registered in Task Scheduler as: AI-CIO-AutoTrade-0945
REM
REM  The orchestrator runs stages 1-9 as named agents: reconcile, guard
REM  exits, research (dry run), screen, zones, bars, then one fail-closed
REM  decision written to outputs\orchestrator\<run_id>\decision.json.
REM  09:45 is RESEARCH ONLY: no order placement invocation is allowed here;
REM  orders are decided in the 10:20 / intraday runs on a proceed verdict.
REM
REM  Everything logged to outputs\auto-trade.log.
REM  Paper only. Never trades live brokers.
REM ============================================================
cd /d C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops
set TA_PY=C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\TradingAgents\.venv\Scripts\python.exe
set LOG=outputs\auto-trade.log
if not exist outputs mkdir outputs
echo.>>%LOG%
echo ==================================================>>%LOG%
echo === auto trade run started %DATE% %TIME% ===>>%LOG%
"%TA_PY%" -c "import json,pathlib,datetime,zoneinfo; p=pathlib.Path('outputs/auto-trade-run.json'); d=json.loads(p.read_text()) if p.exists() else {}; d['started_at']=datetime.datetime.now(zoneinfo.ZoneInfo('America/New_York')).isoformat(); d['status']='started'; p.write_text(json.dumps(d))"

REM Stages 1-9 now run inside the orchestrator as named agents. It records
REM one immutable decision.json per run and never places an order at 09:45.
"%TA_PY%" scripts\desk_orchestrator.py --slot 0945>>%LOG% 2>&1

echo === auto trade run finished %DATE% %TIME% ===>>%LOG%
"%TA_PY%" -c "import json,pathlib,datetime,zoneinfo; p=pathlib.Path('outputs/auto-trade-run.json'); d=json.loads(p.read_text()) if p.exists() else {}; d['finished_at']=datetime.datetime.now(zoneinfo.ZoneInfo('America/New_York')).isoformat(); d['status']='finished'; p.write_text(json.dumps(d))"
".venv\Scripts\python.exe" scripts\run_summary.py>>%LOG% 2>&1
