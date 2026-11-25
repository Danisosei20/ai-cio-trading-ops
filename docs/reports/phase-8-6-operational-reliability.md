# Phase 8.6 — Trading Pipeline Recovery, Full Research Graph Debugging and Operational Reliability Certification

Generated 2026-11-24 (ET). Scope: reliability corrections only; no new trading
strategy, no authorization activation, no live trading, no automatic promotion.

## A. Executive summary

Phase 8.6 repaired the three November 24 scheduled-workflow defects, added a
common terminal-status manifest with integrity protection, bounded the full
research graph with isolated artifacts, reconciled the flatten workflow against
the broker without weakening protective closes, and added an Operational
Reliability panel to `/trading`. A controlled ten-stage daily rehearsal passed
in both nominal and injected-failure scenarios. All 1,386 automated tests pass
(2 skipped), with Ruff, Mypy, secret scan, execution-bypass guard and the 67-check
certification harness green.

Two limitations remain: the full TradingAgents graph has only been verified in
a reduced analyst/tool-round configuration (champion-equivalent latency is NOT
VERIFIED), and the six scout/factory real-bar disagreements remain explained
but unresolved with no live change. Therefore the verdict is
**PHASE 8.6 VERIFIED WITH RESTRICTIONS**. This is not unattended-trading
certification.

## B. Verified repository baseline

- Main commit `a991398`, PR #81 merged, Phase 8.5 "VERIFIED WITH RESTRICTIONS".
- 1,359 tests passing / 2 skipped at baseline; 67 PASS certification harness.
- NVIDIA NIM provider: `nvidia/nemotron-3.5-lightning-30b-a3b`; no OpenAI
  integration introduced; VIX thresholds, RISK_OFF, authorization enforcement
  OFF, overnight dry-run and protective exits preserved.
- Staged/untracked user files in the main checkout were preserved untouched:
  `README.md`, `robinhood_tools/market_prices.py`, `scripts/desk_autopilot.py`,
  `scripts/options_time_machine.py`, `scripts/scenario_test.py`,
  `tests/test_market_prices.py`, `tests/test_option_orders.py`,
  `tests/test_options_flow.py`, `tests/test_options_time_machine.py`,
  `tests/test_options_watch_direction.py`, and all `docs/superpowers/...`
  untracked files. ADR 0001 and ADR 0002 are unchanged.

## C. Intraday wrapper exit-255 root cause

Evidence: `AI-Desk-IntradayLoop` last result 255 at 10:00 ET;
`scripts/intraday_loop.bat` was rewritten at 11:31 ET during the run; the .bat
invoked its Python children with `>> log 2>&1` and checked no `%ERRORLEVEL%`.
Task Scheduler Operational log is disabled, so no Event 201/203 process trace
exists. Conclusion: wrapper/process-level failure consistent with a mid-run
batch rewrite (cmd.exe byte-offset seeking) combined with discarded child
exit codes. A Python exception or interpreter fault cannot be ruled out from
logs, but no such traceback was captured because the wrapper discarded it.

Fix: both launchers capture every child rc, propagate the first nonzero, and
carry a do-not-edit-during-session warning. `scripts/workflow_runner.py` adds
structured start/finish timestamps, child rc, redacted stdout/stderr, artifact
postconditions and a terminal manifest. Controlled success returns 0; injected
failure returns its actual child rc and writes a structured failure artifact.

## D. Flatten workflow repair

The 15:45 task reported result 0 with a stale/empty `outputs/flatten.log` and
no durable artifact. `scripts/flatten_1545.bat` and the intraday backup now run
`flatten_daytrades.py` **first** (preserving real protective closes under
existing authorization policy), then `scripts/flatten_verified.py`, which is
read-only and performs broker reconciliation via Alpaca paper GETs. It reads
day-trade ownership from dated `auto.json` artifacts and overnight ownership
from `outputs/paper/overnight.json`, isolates option symbols, and emits:

- `NO_ACTION_REQUIRED` only with broker evidence (no held equity outside the
  overnight strategy and no open orders), or
- `RECONCILIATION_REQUIRED` with exit 1 when exposure or pending exits remain,
  or when the broker cannot be read.

No order is placed by the verifier; oversized closes and position reversals
remain prevented by the existing close-only logic. Final broker state:
equity 84,247.28, 0 positions, 0 open orders, 0 November 24 fills — broker
evidenced, not inferred.

## E. Overnight workflow repair

The 15:40 result 1 was caused by `raise SystemExit(dict)` on the RISK_OFF block;
the dry-run had completed and written `outputs/paper/overnight.json`. That
exit-code bug was fixed after the run. Two remaining defects were fixed here:
the unguarded `yf.Ticker("SPY").history(...)` was replaced by fail-closed
`_spy_closes()` (empty data → UNKNOWN regime → new risk blocked), and
`overnight_gap` candidates now report `unsupported overnight setup`
explicitly instead of an opaque validator rejection. All candidates remain
blocked, so no overnight entry is enabled. `--dry-run` remains hardcoded in
`scripts/overnight_enter.bat` and the macOS plist example. The workflow reports
symbols evaluated, candidate decisions, rejection reasons, data freshness,
strategy status, dry-run status, completion timestamp and process rc.

## F. Full TradingAgents graph diagnosis

Original evidence: 420 s full-config run timed out without an artifact; a
later 900 s attempt was cancelled by a server restart; no per-stage timing or
terminal artifact existed. Bottleneck attribution from that evidence alone was
impossible, so Phase 8.6 added instrumentation rather than guessing:
`scripts/full_graph_harness.py` records monotonic stage timings flushed to
`progress.json`, enforces a total deadline, isolates all outputs under
`outputs/shadow/research/<run_id>`, forbids champion paths and symlinks, and
writes a terminal `measurement.json` on success, error or timeout. Child
stderr is redacted and no provider key is persisted.

## G. Full-graph latency measurements

| Run | Configuration | Deadline | Wall time | Result |
|---|---|---|---|---|
| Phase 8.5 | full analyst/tool config | 420 s | >420 s | TIMEOUT, no artifact |
| Phase 8.5 | full config | 900 s | — | cancelled by server restart |
| Phase 8.6 `fg-20261124-v2` | market analyst only, 1 tool round, 1 debate/risk round, NVIDIA NIM | 600 s | **215.666 s** | `ok`, decision `Underweight`; child 208.917 s |

This proves a complete full TradingAgents graph can finish under a bounded
reduced configuration with NVIDIA NIM and no orders. It does **not** establish
champion-equivalent latency: median/P95, completion rate and tool-call counts
cannot be reported from one sample. **FULL GRAPH (champion-equivalent) NOT
VERIFIED.** No partial pipeline result was substituted for a complete graph.

## H. Live scout/factory discrepancies

The six immutable November 24 snapshots and all six real-bar comparisons
still disagree. Phase 8.6 classified them as shadow-research discrepancies
(timing/setup-classification and unsupported-data candidates among the causes)
but did **not** replace the operational scout or loosen the canonical factory.
All comparisons and any new measurement remain shadow-only; no live behavior
changed and no strategy was promoted. Independent historical validation and
existing approvals are required before any canonical-specification change.

## I. Scheduler artifact reliability

`robinhood_tools/workflow_manifest.py` defines the common schema: task id,
run id, trading date, scheduled/start/finish times, exit code, execution mode,
input/output evidence references, broker involvement, authorization status,
terminal status and error details. Terminal statuses: VERIFIED, FAILED,
SKIPPED, NO_ACTION_REQUIRED, RECONCILIATION_REQUIRED, NOT_TESTED; INCOMPLETE is
reserved for non-terminal states. Writes are atomic with fsync and an HMAC-SHA256
integrity tag (key from `market_snapshot.py`) using constant-time comparison;
tampering and plain-SHA256 forgery are rejected. Manifests are one per task per
session and preserve prior runs by date. `scripts/eod_lifecycle_report.py` now
reads only each task's own manifest ids, so a manifest FAILED overrides a
scheduler result 0 without cross-task leakage.

## J. End-to-end rehearsal results

`scripts/daily_reliability_rehearsal.py` uses an isolated temporary root, frozen
clock (2026-11-24 17:00 ET) and simulated broker outcomes explicitly labeled
as "SIMULATED — never reconciled as an actual Alpaca fill". It covers 09:35
overnight exit, 09:45 gate, 09:50 watchdog, 10:20 authorization handoff,
intraday loop, 15:10 options watch, 15:40 overnight dry-run, 15:45 flatten,
16:10 news and 16:30 shadow scoring. Results:

- Nominal: PASS — 10 stages, no failures, 0 orders, overnight dry-run.
- Injected flatten failure: PASS — `15:45 intraday flatten` reported FAILED in
  the lifecycle, dashboard `last_failed = flatten-verify`, 0 orders; no failure
  was hidden and no stage falsely reported success.
- Artifacts: `outputs/rehearsals/nominal.json`,
  `outputs/rehearsals/injected-failure.json`.

Timed-out tasks are handled by the harness timeout path with a terminal
timeout artifact (unit-tested). Production scheduler permissions were not
altered; no real Alpaca paper order was submitted.

## K. Dashboard improvements

`/trading` gains an Operational Reliability section backed by the loopback-only
read-only `GET /api/operational-reliability`. It shows workflow health (last
successful run, last failed task, per-task terminal status, exit code, finish
time), research graph health (stage, duration, provider, scope, timeout/error),
signal integrity (canonical factory, scout/factory counts) and broker
verification source. It contains no `do_POST`, escapes all interpolated values,
and explicitly labels shadow research and replay as never being broker fills.
When manifests are missing it reports `data unavailable` rather than inventing
a status.

## L. Automated testing

| Check | Result |
|---|---|
| Full automated tests | **1,386 OK, 2 skipped** (baseline 1,359 / 2) |
| Ruff | All checks passed |
| Mypy | Success: no issues in 93 source files |
| Secret scan | clean |
| Authorization bypass guard | PASS (31 allowlisted modules) |
| Certification harness | 67 PASS, 0 FAIL, 0 WARN, 2 SKIP |

New tests cover wrapper rc propagation, missing/wrong interpreter paths at the
runner boundary, subprocess timeout, structured failure artifacts, artifact
freshness, manifest HMAC forgery and tamper rejection, flatten with zero / long
/ short positions, oversized-close prevention by absence of order submission,
pending-exit and overnight-position reconciliation, broker-read failure,
overnight dry-run behavior and candidate rejection, scheduler path handling,
task isolation, lifecycle status precedence, historical artifact preservation,
live scout/factory comparison hooks, graph timing/isolation, shadow isolation,
broker-vs-simulation labeling, dashboard accuracy and no unauthorized orders.

## M. Adversarial review

Independent review cycles classified and resolved:

- **CRITICAL** — read-only verifier replacing protective closes: fixed by
  restoring `flatten_daytrades.py` before the verifier in both launchers.
- **CRITICAL** — `NO_ACTION_REQUIRED` with unverified broker state: fixed by
  requiring a broker-flat account when no day-trade records exist.
- **HIGH** — forgeable unkeyed manifest hash: fixed with HMAC and
  constant-time comparison.
- **HIGH** — VERIFIED with stale artifact and failed child exit 0: fixed with
  artifact freshness checks and strict rc propagation.
- **MEDIUM** — cross-task manifest leakage, host-local dashboard date: fixed.
- **MEDIUM** — overnight/options position confusion: fixed via ownership
  exclusion lists; residual ambiguity remains where broker tags are absent
  (fail-closed).
- **LOW** — partial redaction and unredacted child stderr: fixed. Trivial
  source-text assertions were supplemented with behavioral tests. An
  unreachable XSS sink in dead code remains documented, not reachable from the
  served page.

Affected tests were rerun after every fix; all green.

## N. Git changes and PR

Work performed on branch `feat/phase8-6-reliability` in worktree
`phase8-6-reliability`, based on `a991398`. Files changed include the new
manifest/runner/verifier/harness/rehearsal/dashboard modules, the four scheduler
launchers and pilot fixes, the lifecycle and dashboard integrations, the two
new test modules, this report and the scheduler audit. CI and PR status are
recorded at submission time in the PR itself; no merge occurs without green CI.

## O. Remaining risks

1. Champion-equivalent full-graph latency and median/P95 remain unmeasured;
   only a single reduced-config run completed.
2. Six scout/factory disagreements remain unresolved; no live change was made.
3. Task Scheduler Operational log is disabled, limiting event-level incident
   proof; enabling it requires owner action.
4. Flatten verification depends on dated local ownership artifacts; missing
   artifacts produce `RECONCILIATION_REQUIRED`, never a false success.
5. Owner approval remains required for any operational activation;
   authorization enforcement stays OFF, overnight stays dry-run, no live
   trading or unapproved paper orders.

## P. Final readiness verdict

**PHASE 8.6 VERIFIED WITH RESTRICTIONS.**

Scheduled workflow reliability, terminal evidence and broker-reconciled flatten
verification are implemented and rehearsed; full research is bounded and a
reduced-config complete graph was measured. The restrictions in section O
must remain explicit. This verdict does not constitute unattended trading
certification or permission to enable live orders.
