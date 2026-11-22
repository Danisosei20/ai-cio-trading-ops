# Nextus — desk overview

A plain-language map of what this system does, what it will not do, and how
the pieces fit together. Code-level decisions live in the plans and specs
under `docs/superpowers/`; this document is the orientation layer.

## Purpose

A paper-only AI trading desk that researches the market, produces a single
go/no-go decision each trading day, and exposes every input and output in a
read-only local viewer. It never places live trades.

## Safety posture

The constraints below are load-bearing. Nothing in the viewer or the UI work
changed them.

- `TRADING_ENABLED=false`
- `PAPER_TRADING_ENABLED=true`
- `TRADING_MODE=paper_auto`
- The viewer binds `127.0.0.1` only, serves fixed assets, and implements
  `do_GET` only — no write, order, or upload endpoint exists.
- Every displayed value comes from a real local artifact and carries its
  source. Missing, empty or unreadable artifacts produce an explicit
  "data unavailable" state, never a plausible empty chart.
- The decision engine fails closed. `proceed` requires a tradeable session, a
  fully paper-only safety snapshot, and `ok` reports from every required
  stage. An override can downgrade `review` to `proceed` but can never clear
  a `halt`, and every override is appended to an audit trail.

## The go/no-go loop

### 09:45 — research and decision only

Twelve named stage agents run under `scripts/desk_orchestrator.py`: Reconciler,
ExitGuard, OddsAnalyst, ScenarioAnalyst, PerformanceAnalyst, Certifier,
Screener, ZoneAnalyst, ResearchFleet, BarReader, Reporter and OrderAgent.

`OrderAgent` is never invoked at 09:45. The run writes one immutable
`outputs/orchestrator/<run_id>/` directory containing `decision.json`, one
report per stage, and the BarReader artifact. A proof matrix
(`prove()`) renders each stage, status, evidence and decision contribution.

### 10:20 and intraday — orders only after `proceed`

`OrderAgent` runs only when the pre-order decision is `proceed`, and places
paper orders only. A `halt` or `review` verdict skips it.

### Scheduling

`scripts/auto_trade_weekday.bat` is registered in Task Scheduler as
`AI-CIO-AutoTrade-0945`. Companion jobs cover the 10:30 entry, 15:45 flatten,
intraday loop, options watch, watchdog and weekly review.

The viewer autostarts via `AI-Desk-Office-Viewer` in HKCU Run →
`scripts/office_server.bat`. That launcher is idempotent: it checks for an
existing agent process and an existing listener on port 8766 before starting,
so a second instance cannot serve stale code alongside the first.

## The viewer

Open <http://localhost:8766/shell>. One shell, five tabs, in-place iframe
switching, `?view=` deep links, and a fail-closed safety ribbon that polls
`/api/safety` every 30 seconds.

| Tab | Route | What it shows |
|---|---|---|
| Office | `/` | 3D agent office, live task states, signal stream, safety panel, and a strip of all 506 loaded symbols linking into Market |
| Market | `/market` | TradingView-style chart: candles, volume, POC/value area, supply/demand zones, sourced support/resistance and SL/TP levels, crosshair with price/time axis boxes, hovered-bar OHLC legend, session dividers, edge markers for off-scale levels, and a 506-symbol picker |
| Pipeline | `/pipeline` | What runs at 09:45, 09:50, 10:00–15:30, 15:10 and 15:45, and through which gates |
| Overview | `/trading` | Live paper account: regime, weekly objective, positions, P&L, orders, reconciliation and scorecard |
| Options | `/options` | Candidate contracts with strike, expiry, DTE, premium, delta, EV% and EV$, modelled target/stop odds, plus policy rejections with their real reasons — every row sourced, no orders placed |

## Data pipeline

- The full S&P 500 universe plus locally researched tickers — 506 symbols —
  have locally stored bars and zone profiles under `outputs/`.
- `GET /api/symbols` derives the available list from artifact stems on disk
  (bars ∩ zones), never from a manifest, and fails closed when empty.
- Option candidates come from the desk's own time-machine and policy-gate
  artifacts. `scripts/options_candidates.py` reports both accepted contracts
  and genuine policy refusals (`approved: false` + `reason` or `error`), each
  with its source artifact.
- Glob artifacts are selected by modification time with a lexicographic
  tie-break. A stage that writes a fresh artifact for one ticker must not be
  judged stale against an older ticker that sorts later alphabetically.

## Verification state

- 851 unit tests passing, ruff clean, `node --check` clean on all viewer JS.
- Every change went through a reviewed PR with green CI before merge.
- Whole-branch reviews caught and fixed real defects: bearish decisions
  rendered as calls, missing sourced SL/TP fields, dropped policy rejections,
  and stale-artifact selection in the orchestrator.
- The first real 09:45 run (`20261122-152407`) returned `halt` on a Sunday
  with `OrderAgent → skipped`, and — after the recency fix — `ResearchFleet → ok`.
  The only remaining decision reason was the untradeable session.

## What it deliberately does not do

- No live trading, no broker call from the viewer, no new order path.
- No market scanning or trade generation from the UI — the Options tab reports
  what the desk already produced; it does not propose new trades.
- No fabricated or unsourced values, and no write endpoint.

## Operational notes

- To check the desk: `python scripts/agent_office.py` from the repo root, then
  open <http://localhost:8766/shell>. Use `scripts/office_server.bat` for the
  detached, autostarted instance.
- To review the latest decision: `outputs/orchestrator/latest.json` points at
  the most recent run; each run directory holds `decision.json`, `reports/`
  and its bar artifact.
- To see a proof matrix: `scripts/desk_orchestrator.py` exposes `prove()` over
  a recorded run.
