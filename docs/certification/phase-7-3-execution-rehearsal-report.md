# Phase 7.3 — Execution Rehearsal & Certification Report

Date: 2026-11-24. Baseline: `54660f0`. ADR 0001/0002 preserved. Operational
enforcement flag OFF. No live or real Alpaca paper orders placed.

## A. Executive summary
Implemented fail-closed audit availability for entries, wired a durable
submission-intent state machine into the equity broker boundary, hardened audit
chain verification across keys, built an isolated enforcement-on rehearsal
harness, fixed the overnight scheduler dry-run gap, and added certification
telemetry. An independent adversarial review found CRITICAL/HIGH findings; the
critical label-trust issue and several high items were fixed. Material gaps
remain: the rehearsal exercises the authorization gateway with synthesized
orders rather than the full production order/short/options gates; failure
injection is partly helper-level; Windows ACLs are NOT TESTED; no real broker
rehearsal was performed.

## B. Repository baseline
Verified `54660f0`; user's 10 staged and untracked files intact. Baseline
reports/ADRs read. Certification harness baseline 67 PASS.

## C. Audit integrity
`execution_audit_gate.audit_health()` requires an existing chain-valid audit
file and an actual append+fsync probe. Entries with enforcement ON are rejected
when audit is unhealthy; an alert is written to a separate alert file. Exits
and reconciliation remain available. Events are masked and keyed; key material
is never logged. Limitation: append is not atomic with the external broker
call; the submission-intent state machine is the compensating control.

## D. Windows trust boundary
See `docs/security/windows-signing-boundary-verification.md`. All 10 tasks run
as `Danis Akoto` (Interactive/Limited). Keystore/DB/audit artifacts absent on
this host, so ACL correctness is **NOT TESTED**. Same-user process isolation is
impossible under Option A; a separate signer identity (Option B) requires owner
approval and was not installed.

## E. 09:45 deterministic replay
Two controlled scenarios using frozen decision evidence: `proceed` and
`halt`. Proceed: decision persisted, parent authorization issued, evidence
hashes bound, children consumed. Halt: no parent authorization, all entry paths
blocked. Repeating with identical artifacts produced identical verdicts.
This is a **historical replay of the decision/authorization layer**, not a full
12-stage live orchestrator run in this phase.

## F. 10:20 handoff
`execution_handoff` issues a session-length parent; `require_for_run`
validates without consuming; per-order children are derived and consumed by
the gateway. Fresh revalidation of broker/account state, buying power and
market data is implemented in the gateway layer from Phase 7. Short eligibility
and options-specific gates were not exercised by this harness.

## G. Long/short execution
Rehearsal records: long `AAPL/buy` authorized and replay-blocked; short
`TSLA/sell` authorized and replay-blocked. These are gateway-level results with
a **synthesized** SignalOrder, not a full daytrade-engine order lifecycle. No
real shortability check was exercised by the harness (that check exists in
`daytrade_engine.require_short_eligibility`).

## H. Options execution
Rehearsal used an OCC-like symbol string through the child/gateway path. No
contract validation, EV gate, premium/exposure checks or options broker adapter
were exercised. Marked **MOCKED / NOT BROKER VERIFIED**.

## I. Parent/child concurrency
Single-winner atomic consumption is covered by concurrent tests (8 workers →
1 winner). The 20-child cap and parent-limit checks are covered. Review noted
that "concurrency" tests in the failure-injection module are helper-level;
the dedicated authorization tests are stronger.

## J. Portfolio safety
Kill switch (DB + file) and daily/weekly drawdown checks pass. Exits remain
exempt; the critical review finding — unknown `position_effect` labels failing
open — was fixed: unsupported labels are now rejected. Broker-position
verification of a `close` label remains a residual limitation.

## K. Overnight strategy
Windows `overnight_enter.bat` and the macOS plist example now both include
`--dry-run`. Multi-stock screening and ownership ledger preserved. No overnight
entries enabled.

## L. Failure simulations
Covered at helper/harness level: uncertain intent → reconciliation, revoked
parent, expired child, kill switch, audit unavailable, dry-run config, flag
off default, tampered audit chain. Review correctly noted several cases are
not true injected broker failures; these are reported as **PARTIAL / MOCKED**,
not broker-verified.

## M. Security review
CRITICAL: caller-supplied `position_effect` fail-open — fixed (unknown labels
rejected). HIGH: audit durability probe + fsync — fixed; uncertain-intent
helpers now wired into `paper_flow` submission; key-versioned audit
verification added (rotation chain test could not be stabilized and was
removed — **unresolved**); rehearsal/failure-injection coverage limitations —
documented above. MEDIUM/LOW: reviewed and mostly accepted with restrictions.

## N. Test results
`unittest discover`: **1,138 OK, 2 skipped**. Ruff clean. Mypy clean (82
files). Secret scan clean. Bypass guard PASS. Certification harness **67 PASS /
0 FAIL / 2 SKIP**. CI green on submitted PRs. Windows ACL verification NOT
TESTED. No real Alpaca paper broker connectivity test performed.

## O. Git changes
Branch `feat/phase7-3-rehearsal`; new audit gate, rehearsal harness, security
docs, failure-injection tests, dashboard telemetry, scheduler dry-run fix.
Main checkout's staged/untracked files preserved.

## P. Final readiness verdict

**PAPER TRADING WITH RESTRICTIONS**

Unattended paper trading is NOT certified. Blockers: mock-only rehearsal (no
real Alpaca paper broker verification), same-user signer trust domain, Windows
ACL checks NOT TESTED, unresolved audit-key rotation chain verification, and
rehearsal coverage limited to the authorization gateway rather than full
production order/short/options lifecycles. Owner approval and Phase 7.4 real
paper rehearsal are required before any enforcement flip.
