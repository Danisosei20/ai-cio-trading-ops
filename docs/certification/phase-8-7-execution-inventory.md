# Phase 8.7 — Architecture and Windows Execution Inventory

Verified 2026-11-25 ET against worktree `phase8-7-certification` at `a075e28`
and read-only queries of the live Windows host. No scheduler configuration was
changed. The owner's staged/untracked files in the main checkout were not
touched.

## 1. Deployment boundary (most important finding)

All registered Task Scheduler definitions point their `<Command>` at the
**live checkout** (`C:\Users\DANISA~1\OneDrive\Documents\AI-trading\ai-cio-trading-ops\scripts\*.bat`).
The Phase 8.6/8.7 launcher fixes exist only in this worktree. The live
`flatten_1545.bat` still runs a single unprotected-to-verifier sequence, and
the live `intraday_loop.bat` still discards child exit codes. **No production
scheduler definition was modified in this phase.** Owner action is required to
deploy the hardened launchers.

Task Scheduler Operational log remains `enabled: false`. Event 201/203 process
traces are unavailable; application-level manifests are the only terminal
evidence. System-wide logging was not enabled.

## 2. Registered tasks

All tasks use `InteractiveToken`, `MultipleInstancesPolicy=IgnoreNew`, battery
guards on, no explicit execution time limit (72 h default), weekday triggers,
and no `<Arguments>`/`<WorkingDirectory>` in the registration. Each launcher
performs its own `cd /d`; Phase 8.7 adds `if errorlevel 1 exit /b 2` after
every `cd /d`.

| Task | Trigger | Launcher | Interpreter | Child(ren) | Log / artifact | Completion evidence | rc propagation |
|---|---|---|---|---|---|---|---|
| AI-Desk-OvernightExit | 09:35 | `overnight_exit.bat` | TA venv | `overnight_pilot.py --exit` | `outputs/overnight.log`, `overnight.json` | state cleared | captured, `exit /b` |
| AI-CIO-AutoTrade-0945 | 09:45 | `auto_trade_weekday.bat` | TA venv (summary: desk .venv) | `desk_orchestrator.py --slot 0945`, `run_summary.py` | `outputs/auto-trade.log`, `outputs/orchestrator/<run_id>/decision.json` | decision artifact; run status now follows `ORCH_RC` | both rcs captured |
| AI-Desk-Watchdog-0950 | 09:50 | `watchdog_windows.bat` | desk .venv | `watchdog_windows.py` | `outputs/watchdog.log` | log line | captured |
| AI-Desk-IntradayLoop | 10:00 | `intraday_loop.bat` | TA venv | `intraday_loop.py` → scout/judge/flatten children | `outputs/intraday.log`; research manifest `outputs/runs/<date>/research.json` | terminal manifest on judge success/failure/timeout | loop/close/verify rcs captured; research failures now return nonzero |
| AI-Desk-OptionsWatch-1510 | 15:10 | `options_watch.bat` | TA venv | `reconcile_fills.py`, `options_watch_trade.py --execute` | `outputs/options-watch.log` | log line | both rcs captured |
| AI-Desk-OvernightEnter | 15:40 | `overnight_enter.bat` | TA venv | `overnight_pilot.py --enter --dry-run` | `outputs/overnight.log`, `overnight.json` | dry-run state | captured |
| AI-Desk-Flatten-1545 | 15:45 | `flatten_1545.bat` | desk .venv | `flatten_daytrades.py` → `flatten_verified.py` | `outputs/flatten.log`, `flatten-verify.json` | broker-reconciled manifest | close rc takes precedence, verifier rc otherwise |
| AI-Desk-NewsReadout-1610 | 16:10 | `news_daily.bat` | desk .venv | `news_daily.py --top 8` | `outputs/news/news_daily.log` | log line | captured |
| AI-Desk-ScoreShadows-1630 | 16:30 | `score_shadow.bat` | TA venv + desk .venv | `settle_memory.py`, `score_shadow.py` | two logs | both rcs captured | settle rc takes precedence |
| AI-Desk-WeeklyReview-1645 | Fri 16:45 | `weekly_review.bat` | desk .venv | `weekly_review.py` | `outputs/weekly-review.log` | log line | captured |
| AI-Desk-Office-Viewer | HKCU Run | `office_server.bat` | desk .venv | `start agent_office.py` | `outputs/office.log` | port 8766 single listener | idempotence checks; `start` rc not meaningful |

`AI-Desk-DayTrade-1030` scripts exist but the task is **not registered**; its
comment is stale.

## 3. Process and evidence rules

- Run identity: per-task UUID-augmented run id in `workflow_runner.py`;
  `research-<HHMMSS>` in the intraday judge; `flatten-<date>` for verification.
- Every `workflow_runner.run_stage` records UTC start/finish, ET run id, child
  rc, redacted stdout/stderr (including Bearer/Slack/AWS/NVIDIA tokens),
  interpreter identity/validation, artifact freshness and an HMAC-SHA256
  terminal manifest. Timeout uses a new process group and terminates only the
  child tree.
- Artifact postconditions require non-empty, non-stale, non-future-dated files
  created at or after the run start. A prior-session artifact cannot certify a
  current run.
- Manifest reads reject HMAC tampering and cross-task/cross-date replay.
- Terminal vocabulary: VERIFIED, FAILED, SKIPPED, NO_ACTION_REQUIRED,
  RECONCILIATION_REQUIRED, NOT_TESTED; INCOMPLETE is non-terminal.

## 4. Repeated failure modes

- Mid-run launcher rewrite: cmd.exe seeks batch files by byte offset; a
  rewritten launcher can produce exit 255. A do-not-edit warning is in the
  intraday launcher; atomic replacement is an owner process action.
- Concurrent duplicate runs: scheduler `IgnoreNew` does not prevent manual
  duplicates; unique run ids and duplicate-exit checks in flatten reduce
  corruption risk.
- Task interruption: a killed parent leaves no manifest, so lifecycle reports
  INCOMPLETE/NOT TESTED rather than success.
- Wrong interpreter: `workflow_runner` accepts `--expected-python` and refuses
  to launch a mismatched interpreter; TA stages keep the pinned TA venv.

## 5. Certification limits

Launcher behavior is verified by isolated file/rc tests and runner fault
injection in this worktree. **Actual scheduled-run verification is NOT
claimed** because the registered tasks execute the live checkout, the
Operational log is disabled, and no owner-authorized deployment occurred.
