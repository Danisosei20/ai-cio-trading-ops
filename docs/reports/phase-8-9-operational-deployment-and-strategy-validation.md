# Phase 8.9 — Operational Deployment, Real-Session Certification, Signal Expiration & Out-of-Sample Validation

Verified 2026-11-25 ET. Engineering verdict: **PHASE 8.9 VERIFIED WITH RESTRICTIONS**.
No verdict here authorizes unattended paper execution or live trading.

## A. Executive summary

Phase 8.9 re-verified the 24-file parity gap, hardened and rehearsed the
deployment helper without applying it, added a reviewable signal-expiration
specification and classifier, expanded research telemetry and verified a real
NIM graph, built immutable point-in-time dataset and walk-forward frameworks
with honest NOT VERIFIED outcomes, re-certified flatten/overnight/authorization
safety, prepared a paper-pilot readiness package, extended the read-only
dashboard, and passed all quality gates. Three adversarial review rounds were
run; all CRITICAL/HIGH findings were fixed and retested.

The operational checkout remains undeployed at `a991398`; therefore no
`ACTUAL_SCHEDULER_VERIFIED` or first verdict is claimed.

## B. Repository baseline

Development worktree `phase8-9-deployment`, HEAD `1c5dc8c` (PR #84), clean.
Operational checkout `a991398`, four commits behind, owner staged/untracked
files preserved untouched. Both venvs are Python 3.11.9; the worktree has no
venv and is a source-only site. ADR 0001 and ADR 0002 are byte-identical in
both checkouts. No secrets were read or copied.

## C. Checkout parity

Detailed matrix: `docs/certification/phase-8-8-checkout-parity.md`; refreshed
hashes: `docs/certification/phase-8-8-parity-data.json`. Current result:
**24 DEPLOYMENT_REQUIRED, 2 MATCH (ADRs), 0 CONFLICT, 0 NOT_VERIFIED**. The
allowlist is exact and unchanged (24 components + 2 ADRs); six source hashes
changed because of Phase 8.9 work.

## D. Deployment readiness and authorization

**DEPLOYMENT_READY_AWAITING_OWNER.** No owner authorization artifact exists; the
Phase 8.8 plan records `owner_authorized: false`, `applied: false`, and ADR 0002
approval is design scope only. The current session also lacks write permission
on the OneDrive checkout. No file, task, logging setting or permission was
changed. See `docs/certification/phase-8-9-deployment-verification.md`.

## E. Deployment and rollback verification

`scripts/deploy_hardened_launchers.py` now blocks missing sources by default,
binds `--source` to certified commit `1c5dc8c`, verifies source/destination
hashes, blocks owner conflicts, refuses 09:45–16:05 ET and running AI tasks,
writes the backup manifest before each replacement, records created files,
uses atomic replace, verifies post-deployment hashes, and supports hash-verified
rollback. Isolated rehearsals cover success, partial failure with restored
state, conflict, missing sources, created-file removal, active window, running
tasks, duplicate decision keys and interrupted copies. No production deployment
was applied.

## F. Windows scheduler certification

All ten registered `AI-*` tasks point to the operational checkout via 8.3 short
paths, empty arguments and working directory, `InteractiveToken`, `IgnoreNew`,
no explicit time limit. Last results: IntradayLoop **255**, OvernightEnter **1**,
all others 0. Evidence levels collected: unit, direct shell, batch-wrapper,
isolated rehearsal. **Actual registered-task execution and scheduled-time
execution are NOT VERIFIED.** No task was triggered or edited; Operational
logging remains disabled and event-log verification is unavailable.

## G. Research telemetry and full-graph timings

The ToolNode callback wiring was verified in a real isolated NIM run:

| Run | Wall | Graph | Model req/comp | Tool calls | Failures | Evidence | Config |
|---|---|---|---|---|---|---|---|
| `telemetry-20261125-v3` | **401.607 s** | 393.458 s | 15 / 15 | **12 attempted, 12 completed** | 0 | complete | match |

Queue delay 624.598 ms. Cache hits are `null` with an explicit NOT VERIFIED
reason; freshness at decision is NOT VERIFIED because no `market_as_of` was
present. Stage durations, model completions, provider retries and scrubbed
error events are now recorded. No challenger promotion; no orders.

## H. Signal-to-decision expiration analysis

`docs/specs/intraday-setups/v1.0.0/EXPIRATION.md` formalizes existing rules
only: 300 s freshness, 20 min bar staleness, 15:15 cutoff, 15:45 flatten,
session/completed-bar checks. It adds no new permission. The classifier
(`scripts/signal_expiration_report.py`) is first-match-wins and fails closed on
missing or out-of-order timestamps. Applying it to the 32 real observations:
**0 ACTIONABLE_AT_DECISION, 0 EXPIRED_BEFORE_DECISION, 1 SESSION_CUTOFF_EXCEEDED
(AMZN 15:17:31 ET), 31 INSUFFICIENT_EVIDENCE**. Completing before 15:15 is
explicitly not treated as individual signal actionability.

## I. Frozen multi-session dataset

`robinhood_tools/pit_dataset.py` extends the existing snapshot infrastructure
with data-type labels (`signal_time_snapshot`, `captured_live_session`,
`historical_backfill`, `synthetic_fixture`), decision-time leakage guards,
duplicate-key and duplicate-bar-set detection, HMAC-bound index metadata,
split integrity, calendar-driven missing-session checks and tamper
verification. **No genuine multi-session point-in-time dataset exists**: all
stored bars are one post-close session, duplicated across cycle labels, with
half the snapshots failing HMAC under the available key. Historical backfill
and synthetic fixtures are evaluation-only and cannot prove what the scanner
observed at signal time.

## J. Long strategy walk-forward results

The framework (`scripts/walk_forward_evaluator.py`) evaluates the approved
`long_reclaim` rule with features from completed bars only, entry at the next
bar open, target-first vs stop-first outcome resolution over future bars, and
distinct unique-signal counts separated from cost scenarios. It enforces
time-based dev/validation/final splits, rejects split mismatches, never uses
the final holdout for selection, and labels all figures `SCENARIO_ONLY`.
Verdict: **NOT VERIFIED** — no accepted point-in-time dataset; no win rate,
expectancy or profit factor claimed.

## K. Short strategy walk-forward results

Same framework for `short_breakdown`, with short borrow evidence required from
fresh broker snapshots (`shortable`, `easy_to_borrow`, account permissions,
120 s max age). No such evidence is retained. Verdict: **NOT VERIFIED**; no
profitability claim, no strategy change and no promotion.

## L. Scout/factory consistency

SPEC v1.0.0 and both implementations agree at code level: scout long
`price > VWAP and >= OR_low`, short `< VWAP and <= OR_high`; canonical adds
`> OR_high` / `< OR_low`. The stored 2026-11-24 comparison artifact predates
the classifier fix and is stale (all rows `implementation_defect`); current
code classifies these as intentional specification differences. Independent
session evidence is unavailable, so artifact-level corroboration is NOT
VERIFIED. No live scout rule or factory gate was changed.

## M. Market-data entitlement limitations

Real-time SIP returns 403; IEX real-time is permitted with `volume_basis:
iex_only`; historical SIP is permitted but delayed SIP `/bars` is not. No
consolidated volume is inferred and no subscription was activated. Missing
calendar and borrow snapshots prevent authoritative missing-session and
shortability conclusions. Only strategies with IEX-compatible evidence can be
evaluated under these entitlements.

## N. Broker and authorization safety

Flatten keeps protective closes first, caps quantity at owned broker holdings,
derives close side from position sign, detects working `(symbol, side)` closes,
refreshes its local view after each placement, and fails closed on unattributed
equity. Overnight entry remains hardcoded dry-run with deduplicated exits.
Authorization digest, evidence binding, TTL, symbol/side/account/broker
binding, position effect, single-use gateway and revocation are enforced in
isolated tests. Operational enforcement remains OFF; uncertain intents are
visible on the dashboard. Shadow and evaluation paths contain no order calls.

## O. Paper-pilot readiness matrix

`docs/certification/phase-8-9-paper-pilot-readiness.md` documents scope,
existing policy limits, kill switch, authorization TTLs, short eligibility,
automatic stop conditions and rollback. Verdict: **NOT AUTHORIZED**; seven
prerequisites remain unmet, led by deployment, enforcement rehearsal,
signal-expiration evidence and OOS validation.

## P. Dashboard changes

The loopback-only, read-only `/trading` page gains **Session Readiness &
Research Validity** with derived development commit, deployment/applied state,
real rollback-manifest availability, scheduler logging coverage, expiration
class counts, spec version, replay/OOS status, derived authorization mode and
pilot status. Simulated and shadow results are labeled and never shown as
broker fills.

## Q. Automated tests and CI

| Check | Result |
|---|---|
| Full tests | **1,489 OK, 2 skipped** (baseline 1,459 / 2) |
| Ruff | All checks passed |
| Mypy | Success: 95 source files |
| Secret scan | clean |
| Execution bypass guard | PASS (31 allowlisted modules) |
| Certification harness | 67 PASS, 0 FAIL, 0 WARN, 2 SKIP |

New tests cover parity/conflict, deployment blocking and rollback, source-commit
binding, all five expiration classes with ordering checks, PIT HMAC and
duplicate detection, walk-forward splits and next-bar outcome logic, cost
scenario labeling, dashboard truthfulness and no unauthorized order paths.

## R. Independent adversarial review

Three review rounds were conducted. Findings fixed and verified: fabricated
walk-forward wins and scenario-count inflation, unsigned dataset metadata,
split leakage, missing-source deployment success, false actionable-signal
classification, false rollback availability, full-SHA commit mismatch, and
source/destination hash binding. No CRITICAL or HIGH execution-safety or
evidence-integrity finding remains from those rounds.

## S. Git changes and PR

Branch `feat/phase8-9-deployment` from `1c5dc8c`; includes expiration spec and
classifier, PIT dataset module, walk-forward evaluator, safety-hardened
deployment helper, expanded telemetry, dashboard extension, reports and tests.
PR and CI status are recorded at submission; no merge without green CI and
repository approval.

## T. Remaining restrictions

1. Operational deployment is not applied; scheduler results still come from
   the old checkout.
2. No actual registered-task execution evidence; Operational logging disabled.
3. No genuine multi-session point-in-time dataset; replay and OOS NOT VERIFIED.
4. Cache hits and research freshness at decision are NOT VERIFIED.
5. Enforcement OFF; no unattended or live trading; no strategy promotion.

## U. Required owner approvals

1. Written authorization for the 24-file deployment and a maintenance window.
2. Approval to enable authorization enforcement only after isolated rehearsal.
3. Optional approval to enable Task Scheduler Operational logging.
4. A separate decision for any paper pilot; this report is not that decision.
5. Independent validation before any strategy eligibility change.

## V. Final readiness verdict

**PHASE 8.9 VERIFIED WITH RESTRICTIONS.**

The reviewed development code is internally consistent, safety-preserving and
well-instrumented. Operational deployment, real scheduler certification and
out-of-sample strategy validation remain explicitly unverified pending the
owner actions above. No unattended trading is authorized.
