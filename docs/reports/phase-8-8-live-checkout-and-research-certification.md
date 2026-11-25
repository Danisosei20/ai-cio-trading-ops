# Phase 8.8 — Live Checkout Parity, Windows Scheduler Certification, Research Observability & Strategy Validation

Verified 2026-11-25 ET. Engineering verdict: **PHASE 8.8 VERIFIED WITH RESTRICTIONS**.
This verdict does not authorize live trading or unattended paper execution.

## A. Executive summary

Phase 8.8 established a read-only parity matrix between the development
worktree and the operational checkout used by Windows Task Scheduler, prepared
a rollback-capable launcher deployment package without applying it, found and
fixed the research tool-call telemetry defect and verified real tool dispatch
in a clean NVIDIA NIM champion-equivalent run, produced an honest signal-to-decision
latency report from actual research logs, added a point-in-time replay guard,
tightened duplicate-close and authorization binding, extended the read-only
dashboard with Deployment & Decision Readiness, and passed all quality gates.
No operational checkout file, registered task, host logging setting, broker
order or trading permission was changed.

Three adversarial review rounds were performed; all CRITICAL and HIGH
execution-safety and evidence-integrity findings were fixed and retested.

## B. Repository and operational checkout inventory

- Development: worktree `phase8-8-parity`, HEAD `f9e2b68` (PR #83), clean.
- Operational: `C:\Users\Danis Akoto\OneDrive\Documents\AI-trading\ai-cio-trading-ops`, HEAD `a991398`, four commits behind, with owner staged and untracked changes preserved untouched.
- TradingAgents venv and desk venv both exist (Python 3.11.9); the worktree has no venv and is a source, not an execution site.
- Ten registered `AI-*` tasks run from the operational checkout via 8.3 short paths, with empty `Arguments`/`WorkingDirectory`, `InteractiveToken`, `IgnoreNew`, no explicit execution time limit and no restart settings. Last results: IntradayLoop **255**, OvernightEnter **1**, all others 0.
- ADR 0001 and ADR 0002 are byte-identical in both checkouts. Secret files were located by name only; no credential content was read, copied or reported.

## C. Development/live parity matrix

Generated in `docs/certification/phase-8-8-checkout-parity.md` and
`phase-8-8-parity-data.json`: **24 DEPLOYMENT_REQUIRED, 2 MATCH** (the ADRs),
0 CONFLICT, 0 NOT_VERIFIED. All 24 differences are the reviewed newer worktree
revision; none is an owner-local modification of a target file. The deployable
allowlist also includes two inert dependencies: `workflow_manifest.py` and
`agent_office.py`.

## D. Deployment preparation and authorization status

`scripts/deploy_hardened_launchers.py` defaults to dry run; applying requires
both `--apply` and `--owner-authorized`. It verifies source hashes, refuses
CONFLICT/NOT_VERIFIED entries, refuses 09:45–16:05 ET and running AI tasks,
writes the backup manifest before the first replacement and incrementally after
each file, records created files, uses atomic replace, verifies post-deployment
hashes, and supports hash-verified rollback that deletes created files and
applies the same batch-safety guards. The dry run produced 24 COPY, 2 SKIP,
0 CONFLICT, `secrets_touched=false`, `scheduler_modified=false`, `applied=false`.

**Owner authorization was not available; nothing was deployed.** The live
flatten script still lacks short covering and the verifier, and the live
intraday wrapper still discards child exit codes. The two failing task results
are therefore expected until deployment occurs.

## E. Hardened launcher verification

Launcher rc/cwd guards and research manifests were verified by isolated unit
and rehearsal tests in the worktree. No registered task was triggered. Actual
scheduled-run verification is explicitly **NOT VERIFIED**; a manual script run
is not scheduler evidence. The byte-offset rewrite hazard is mitigated by a
do-not-edit warning and the deployment window guard, not by scheduler changes.

## F. Actual Task Scheduler evidence

Read-only XML and last-result data were collected (Section B). No task was
edited, run or disabled. Operational logging was not enabled; coverage remains
unavailable. Terminal evidence therefore comes from application manifests,
never from missing scheduler events.

## G. Research tool-call instrumentation findings

Root cause: `TradingAgentsGraph` forwarded callbacks only to LLM constructors
(`trading_graph.py:78-80`), while `ToolNode` callbacks require handlers in the
graph invoke config; `get_graph_args()` was called without them. The benchmark
now wraps `graph.propagator.get_graph_args()` and injects the same handler into
`config["callbacks"]`, so real tool starts/ends/errors are observed. Telemetry
records run/stage/model/tool identifiers, UTC and monotonic timestamps,
duration, status, error class, retry attempt, cache-hit placeholder and evidence
reference; only allowlisted argument keys are stored, and no prompts, tool
outputs or secrets are written. A reused run id clears stale telemetry before
execution. The benchmark refuses success when tool counts disagree with events
or an event contains a secret pattern.

## H. Champion-equivalent graph performance

| Run | Wall (s) | Graph (s) | Tool calls | Tool events | Evidence | Config match |
|---|---|---|---|---|---|---|
| `telemetry-20261125-v2` (final) | **241.715** | 236.121 | **8** | 8 attempted / 8 completed / 0 failed | complete | true |

Decision `Sell`, 18 model requests, 0 provider errors, 0 retries. An earlier
run with a reused run id was superseded because stale telemetry was appended;
the run-id clearing fix prevents recurrence. This is a single observation of an
isolated graph, not median/P95 and not the full desk decision path. The
challenger remains shadow-only; no promotion.

## I. Signal-to-decision latency analysis

`docs/certification/phase-8-8-signal-latency.json` parsed 32 actual research
start/decision pairs from the live research log for 2026-11-24: median research
duration **320.721 s** (min 227.055, max 447.392); **31 of 32** decisions
completed before the 15:15 ET no-new-entries cutoff. Only one `auto.json`
supplied `price_as_of`, so source-quote age is available for 1 observation and
is within 300 s there; the remainder are NOT_VERIFIED. Setup formation, last
completed bar, signal detection, research queue entry, deterministic gate,
authorization and expiration timestamps are absent from the artifacts and are
reported as missing rather than estimated. A negative quote age is treated as
invalid, not fresh. New `robinhood_tools/latency_ledger.py` provides append-only
UTC/monotonic event recording for future stages.

## J. Scout/factory strategy comparison

`docs/specs/intraday-setups/v1.0.0/SPEC.md` matches the implemented long/short
predicates and OR boundaries. The six November 24 comparisons use duplicated
post-close bar sets and are not live evidence. `scripts/strategy_replay_guard.py`
rejects post-close snapshots, unparseable or future bar timestamps and duplicate
bar sets; the stored non-v2 snapshots also fail HMAC verification. No live scout
rule or factory gate was changed.

## K. Historical replay and out-of-sample results

**NOT VERIFIED.** There is no frozen multi-session point-in-time bar archive;
the only immutable bars are one post-close session for three symbols, with
half the snapshots failing HMAC and duplicated cycle sets. Existing backtests
grade the scout rule, stub the odds gate, retain no trade-level rows and cover
9 trades per report. No canonical signal has ever been validated or traded.
No win rate, expectancy, profit factor, drawdown or regime edge is claimed.

## L. Flatten safety verification

`close_plan()` derives the close side from position direction, caps quantity at
the owned amount and broker holdings, and now detects working orders using the
symbol and side fields Alpaca actually returns. After a successful close it
updates the local position and working-order view, preventing two day-trade
records for one symbol from flipping exposure short. Unattributed equity,
stale snapshots, unknown order states, corrupt ownership and broker outages
produce `RECONCILIATION_REQUIRED`. Verification remains read-only.

## M. Overnight dry-run verification

`--dry-run` remains hardcoded in Windows and macOS launchers. Stale/future bars,
missing history and unsupported setups produce explicit rejection reasons;
order counters reflect actual placements; the exit path deduplicates symbols,
checks working sells and returns nonzero on failures. No entry was enabled.

## N. Execution authorization boundary review

Decision identity, evidence hash binding, parent/child derivation, expiry,
instrument/side/quantity/account/broker binding, atomic single-use and
revocation remain enforced in isolated tests. The signed `position_effect` is
now bound at the gateway, and uncertain submission intents are surfaced on the
dashboard. Operational enforcement remains OFF; no research, scoring or
dashboard path can invoke broker submission.

## O. Dashboard updates

The loopback-only read-only `/trading` page gains **Deployment & Decision
Readiness**: parity counts, deployment applied state and owner authorization,
champion tool calls and tool-event counts, telemetry policy, observed research
runs and median duration, decisions before cutoff, quote-age status, canonical
spec version, replay/OOS status and uncertain intents. Simulated and shadow
results are explicitly labeled; no simulated fill is shown as a broker fill.

## P. Automated tests and CI

| Check | Result |
|---|---|
| Full tests | **1,459 OK, 2 skipped** (baseline 1,425 / 2) |
| Ruff | All checks passed |
| Mypy | Success: 94 source files |
| Secret scan | clean |
| Execution bypass guard | PASS (31 allowlisted modules) |
| Certification harness | 67 PASS, 0 FAIL, 0 WARN, 2 SKIP |

New tests cover parity/hash/conflict detection, dry-run and rollback safety,
active-window guards, scheduler path resolution, interpreter and rc handling,
tool telemetry counters and failure gating, deadline artifacts, latency ledger
metrics, replay leakage guards, OR boundaries, flatten/overnight safety,
authorization binding and dashboard labels.

## Q. Independent adversarial review

Review round 1 found five HIGH findings: partial-deploy rollback without a
manifest, rollback unable to delete created files, rollback bypassing batch
guards, duplicate closes within one flatten run and duplicate overnight sells.
Round 2 found residual CLI/telemetry issues. All were fixed and re-verified:
incremental manifests, created-file rollback with guards, local broker-view
refresh, exit deduplication and working-order checks, CLI owner-flag forwarding
and per-run telemetry clearing. No CRITICAL or HIGH execution-safety or
evidence-integrity issue remains from those reviews.

## R. Operational certification matrix

| Capability | Evidence source | Environment | Result | Restriction / owner action |
|---|---|---|---|---|
| Development checkout | git HEAD `f9e2b68`, clean | worktree | UNIT_VERIFIED | none |
| Operational checkout | git HEAD `a991398`, owner changes intact | live read-only | UNIT_VERIFIED | owner deployment decision |
| Hardened launcher deployment | dry-run plan, rollback tests | isolated | REHEARSAL_VERIFIED | **not applied**; owner authorization required |
| Actual scheduler execution | task XML + last results | live read-only | NOT_VERIFIED | no task triggered; Operational log disabled |
| Intraday wrapper | rc guards + research manifest tests | isolated | INTEGRATION_VERIFIED | live wrapper still old |
| Research tool-call telemetry | 8 dispatch events, 4 counter validation cases | real NIM, shadow | INTEGRATION_VERIFIED | single run; cache-hit field not populated |
| Champion full-graph research | `telemetry-20261125-v2/measurement.json` | real NIM, shadow | INTEGRATION_VERIFIED | n=1; no P95; no promotion |
| Signal-to-decision timeliness | live research log, 32 observations | actual log | INTEGRATION_VERIFIED | 7 timestamp fields missing; quote age n=1 |
| Long setup validity | spec + OR boundary tests | unit | UNIT_VERIFIED | OOS not verified |
| Short setup validity | spec + OR boundary tests | unit | UNIT_VERIFIED | OOS not verified |
| Market-data coverage | replay guard, HMAC checks | isolated | UNIT_VERIFIED | one post-close session only |
| Flatten safety | mocked broker, rc tests | isolated | REHEARSAL_VERIFIED | live checkout lacks fixes |
| Overnight dry-run | launcher + failure tests | isolated | REHEARSAL_VERIFIED | live checkout lacks fixes |
| Authorization boundary | isolated gateway tests | isolated | UNIT_VERIFIED | enforcement OFF operationally |
| Broker reconciliation | read-only verifier tests | simulated | UNIT_VERIFIED | no real broker scenario this phase |
| Dashboard evidence integrity | payload tests, labels | unit | UNIT_VERIFIED | deployment warning remains |

## S. Git commits and PR status

Branch `feat/phase8-8-parity` from `f9e2b68`; changes include parity and
deployment helpers, tool telemetry, latency ledger and report, replay guard,
safety fixes, dashboard extension, new tests and this report. PR and CI status
are recorded at submission; no merge without green CI and repository approval.

## T. Remaining blockers and owner actions

1. Authorize deployment of the 24-file allowlist in a maintenance window, with
   the tested rollback manifest; no scheduler definition changes are required.
2. Optionally enable Task Scheduler Operational logging.
3. Provide frozen multi-session point-in-time bars before claiming replay/OOS
   strategy validation; current data supports none.
4. Collect more champion samples and populate cache-hit telemetry before any
   performance percentile claim.
5. Keep enforcement OFF, overnight dry-run, no unattended/live trading and no
   challenger promotion.

## U. Final readiness verdict

**PHASE 8.8 VERIFIED WITH RESTRICTIONS.**

The development repository is internally consistent, safe and well-instrumented;
the operational checkout is not yet at parity and deployment remains an owner
decision. Actual scheduled-run certification, statistical latency certification
and strategy out-of-sample validation remain explicitly out of reach. This is
an engineering assessment, not trading authorization.
