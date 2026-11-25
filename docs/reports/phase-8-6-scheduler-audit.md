# Phase 8.6 — Scheduler and Wrapper Execution Audit

Baseline: `a991398` (PR #81, Phase 8.5). Audit performed read-only on the
worktree `phase8-6-reliability`; live evidence read from the main checkout at
`C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops`.
No staged or untracked user files were modified.

## 1. Registered tasks and last results (November 24, 2026 ET)

| Task | Last run | Scheduler result | Observed evidence |
|---|---|---|---|
| `AI-Desk-IntradayLoop` | 10:00 | **255** | `outputs/intraday.log`, wrapper rewritten 11:31 mid-run |
| `AI-Desk-OvernightEnter` | 15:40 | **1** | `outputs/overnight.log` ended with `SystemExit(dict)` repr; `outputs/paper/overnight.json` was written |
| `AI-Desk-Flatten-1545` | 15:45 | **0** | stale/empty `outputs/flatten.log`, no terminal artifact, no broker verification |
| `AI-Desk-OptionsWatch-1510` | 15:10 | 0 | VERIFIED in full-file review |
| `AI-Desk-NewsReadout-1610` | 16:10 | 0 | VERIFIED in full-file review |
| `AI-Desk-ScoreShadows-1630` | 16:30 | 0 | VERIFIED in full-file review |

`MultipleInstancesPolicy=IgnoreNew`; stop-if-runs 72:00:00. Task Scheduler
Operational log is **disabled** (`wevtutil gl Microsoft-Windows-TaskScheduler/Operational`
→ `enabled: false`), so Event 201/203 process traces do not exist. Exact
event-level origin of 255 cannot be proven; wrapper-level evidence is conclusive.

## 2. Process trees and return-code propagation (as found)

- `AI-Desk-IntradayLoop` → `scripts/intraday_loop.bat`
  → TradingAgents venv `python.exe` → `scripts/intraday_loop.py`
  (45-minute scanning loop) → in-process `run_flatten()` at 15:30.
  The .bat invoked three child processes with `>> log 2>&1` and **never read
  `%ERRORLEVEL%`**, so a child failure could not reach the scheduler. The
  launcher file was rewritten at 11:31 ET while cmd.exe had it open; cmd.exe
  seeks batch scripts by byte offset, so an in-flight rewrite can jump into
  the middle of a new command and yield exit 255. No traceback exists because
  the parent discarded child output codes.
- `AI-Desk-Flatten-1545` → `scripts/flatten_1545.bat` → `.venv\python.exe`
  → `scripts/flatten_daytrades.py`, again with no `%ERRORLEVEL%` capture.
  Result 0 plus a stale/empty log produced a false success; no durable
  completion artifact and no broker-verified flat state existed.
- `AI-Desk-OvernightEnter` → `overnight_enter.bat` → `overnight_pilot.py`.
  The handled RISK_OFF block returned a `dict` to `SystemExit`, which Python
  prints to stderr and exits 1. The dry-run itself completed and persisted
  `{"date":"2026-11-24","entries":[]}`. The exit-code fix landed at 16:43 ET,
  after the 15:40 run. Two logic defects remained: an unguarded
  `yf.Ticker("SPY").history(...)` and an opaque `overnight_gap` →
  `long_reclaim` validator mismatch.
- Full research: `scripts/tradingagents_paper_auto.py` → TradingAgents venv
  → NVIDIA NIM `nvidia/nemotron-3.5-lightning-30b-a3b`. A 420 s attempt timed
  out with no artifact; a later 900 s attempt was cancelled by a server
  restart. No per-stage timing or timeout artifact existed.

## 3. Interpreter selection

The intraday launcher pins `TA_PY` to the TradingAgents venv and sets
`TRADINGAGENTS_VENV_PYTHON`; it verifies `import tradingagents` before running.
The flatten launcher uses the desk `.venv`. `workflow_runner.py` now requires
an explicit `--python` argument, so no implicit/wrong interpreter can be
silently selected by the reliability layer.

## 4. Broker reconciliation sources

`robinhood_tools.alpaca_paper.AlpacaPaperBackend` supplies `list_positions()`,
`list_open_orders()`, account GET and market clock, all read-only. Strategy
ownership is inferred from `outputs/paper/tradingagents/*/DATE/auto.json`
(day-trade) and `outputs/paper/overnight.json` (overnight). Final November 24
broker state: equity **84,247.28**, **0** positions, **0** open orders,
**0** fills.

## 5. Fixes applied (verified)

1. `scripts/intraday_loop.bat` and `scripts/flatten_1545.bat` capture every
   child `%ERRORLEVEL%` and propagate the first failure. A do-not-edit
   comment warns against mid-session rewrite. Protective
   `flatten_daytrades.py` remains **before** the read-only verifier.
2. `scripts/workflow_runner.py` records start/finish, redacted stdout/stderr,
   child rc, artifact postcondition including **freshness** (rejects an
   artifact older than the run), and writes a terminal manifest; failures
   never become exit 0.
3. `robinhood_tools/workflow_manifest.py` provides append-style atomic
   manifests with HMAC-SHA256 integrity (key from `market_snapshot.py`),
   constant-time comparison and a controlled terminal vocabulary.
4. `scripts/flatten_verified.py` is read-only: `NO_ACTION_REQUIRED` requires
   a broker-flat account; `RECONCILIATION_REQUIRED` otherwise; overnight and
   options holdings are isolated from day-trade reconciliation.
5. `scripts/overnight_pilot.py` uses fail-closed `_spy_closes()` and reports
   `unsupported overnight setup` explicitly; all candidates remain blocked so
   no overnight entry is enabled. `--dry-run` remains hardcoded in Windows and
   macOS launchers.
6. `scripts/full_graph_harness.py` isolates all outputs under
   `outputs/shadow/research/<run_id>`, enforces per-run deadline and writes a
   terminal measurement artifact even on timeout.

## 6. Residual limitations

- Task Scheduler Operational log stays disabled; enabling it is an owner
  action required for event-level proof of the original 255.
- Six scout/factory real-bar disagreements from Phase 8.5 remain unresolved;
  no live strategy behavior was changed and no promotion occurred.
- Flatten verification cannot distinguish overnight shares from day-trade
  shares in the same symbol beyond the recorded ownership artifacts; broker
  APIs do not expose strategy tags. Fail-closed status protects against a
  false claim.
- Champion-equivalent full-graph latency is not verified (only a reduced
  analyst/tool-round configuration completed).

## 7. Verdict

Scheduler and wrapper reliability defects are **repaired and regression
tested**. Original incident attribution beyond wrapper level remains limited
by the disabled Operational log. No operational activation is claimed; owner
approval is still required.
