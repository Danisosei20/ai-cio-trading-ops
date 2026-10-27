@echo off
REM ============================================================
REM  Weekday 09:45 ET autonomous paper run (AI-CIO desk).
REM  Registered in Task Scheduler as: AI-CIO-AutoTrade-0945
REM
REM  0) options_guard --execute   exits first (take +2% / cut -25% / DTE)
REM  1) Refresh the screener (deterministic, top 10)
REM  2) TradingAgents debate on the top 2 screened names
REM     -> desk cross-check -> regime gate -> session gate
REM     (09:45-15:45 ET weekdays + Alpaca clock open)
REM     -> Alpaca PAPER order ($500 cap) -> Slack after execution
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

"%TA_PY%" scripts\options_guard.py --execute>>%LOG% 2>&1
"%TA_PY%" scripts\universe_screen.py --top 10 --out outputs\screener\latest.json>>%LOG% 2>&1
"%TA_PY%" scripts\zone_analysis.py --tickers SPY,QQQ,NVDA,AMD,AAPL>>%LOG% 2>&1
"%TA_PY%" scripts\tradingagents_paper_auto.py --screen outputs\screener\latest.json --screen-top 3 --quick-model openai/gpt-oss-20b --deep-model stepfun-ai/step-5-preview --max-notional 2000>>%LOG% 2>&1

echo === auto trade run finished %DATE% %TIME% ===>>%LOG%
"%TA_PY%" -c "import json,pathlib,datetime,zoneinfo; p=pathlib.Path('outputs/auto-trade-run.json'); d=json.loads(p.read_text()) if p.exists() else {}; d['finished_at']=datetime.datetime.now(zoneinfo.ZoneInfo('America/New_York')).isoformat(); d['status']='finished'; p.write_text(json.dumps(d))"
".venv\Scripts\python.exe" scripts\run_summary.py>>%LOG% 2>&1
