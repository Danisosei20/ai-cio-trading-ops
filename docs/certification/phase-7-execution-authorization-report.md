# Phase 7 — Execution Authorization Certification Report

Date: 2026-11-23. Baseline: `fd6b54b`. ADR 0001 preserved.

## A. Executive summary
Five read-only audits inventoried every order path, risk control, and broker
short-eligibility interface. ADR 0002 was **approved by the owner on 2026-11-23** (design scope only).
Deterministic authorization, short-eligibility, unified entry-risk, CI bypass
guard and dashboard telemetry were implemented and independently reviewed. The
review found two CRITICAL defects and two HIGH gaps; the CRITICAL defects and
the guard gap were fixed and rerun. Enforcement stays disabled by default.
Unattended paper trading is **not** certified.

## B. ADR 0002
`docs/adr/0002-execution-authorization-protocol.md`. Status: **APPROVED 2026-11-23 (design scope); enforcement flag OFF**
explicit owner approval**. Implementation mapping: HMAC-signed deterministic
authorizations (`robinhood_tools/execution_authorization.py`), 09:45 issue and
10:20 validate (`scripts/execution_handoff.py`), broker-boundary consumption
(`robinhood_tools/paper_flow.py`), short eligibility
(`robinhood_tools/short_eligibility.py`), unified entry risk
(`robinhood_tools/entry_risk.py`).

## C. Execution path inventory
`docs/architecture/execution-path-audit.md` lists 12 paths plus adapters. All
paper orders converge on two broker calls. Only `intraday_loop.py` previously
read the orchestrator verdict. Entry paths now have an authorization boundary at
`paper_flow.place_signal_order`, but propagating `authorization_id` from the
intraday judge to each order is **not complete**; with enforcement enabled,
buy entries would be blocked (fail closed).

## D. 09:45 → 10:20 certification
At 09:45 a `proceed` run can issue a bounded, HMAC-signed, artifact-hash-bound
authorization with expiry, account/broker binding and a deterministic idempotency
key. At 10:20 the handoff validates integrity, expiry and bindings and the
broker boundary atomically consumes the authorization. A missing/expired/tampered
authorization blocks the entry. Tests cover the `RunDecision` dataclass → dict
round-trip and the `latest.json` subset used at 10:20 (the two CRITICAL review
findings). No live 10:20 entry was executed.

## E. Short-selling certification
`short_eligibility.py` uses `GET /v2/assets/{symbol}` (`shortable` and supported
`borrow_status`; deprecated `easy_to_borrow` ignored) and `GET /v2/account`
(`shorting_enabled` and restriction flags). Missing, hard-to-borrow, stale,
mismatched or errored evidence rejects the entry. Wired before any short order
in `daytrade_engine._place_order`; exits remain unblocked. No live short orders
placed. Broker locates are not reachable through the current `/v2/`-only
transport; ETB-only is the fail-closed policy.

## F. Portfolio risk certification
`entry_risk.py` unifies both kill switches for entries, enforces separate 3%
daily and 7% weekly drawdowns from explicit ET baselines, keeps all-time peak as
a diagnostic, and exempts exits. Tests pass. The existing split controllers
remain for backward compatibility during migration; full convergence is not
complete.

## G. Long/short trading certification
Long and short paper lifecycles remain covered by the existing 67-check harness
and 1,045 tests. Short eligibility fixture updates were required in two test
harnesses. No live brokerage orders were placed.

## H. Overnight strategy
15:40 entries remain dry-run on Windows; multi-symbol screening and ownership
ledger preserved. Audit-only authorization evaluation does not submit orders.
The macOS plist example still lacks `--dry-run` — an unresolved inconsistency.

## I. Security and failure simulation
Passed: tampered authorization, wrong account/broker/symbol/side, over-limit
size, expiry, replay, invalid state, 8 concurrent consumers (single winner),
stale/missing/hard-to-borrow short evidence, kill switch, daily/weekly drawdown,
exit exemption, no-LLM assertion. Gaps: HMAC key trust relies on same-user file
permissions; `decision_id` is not recomputed at validation; gateway is not yet
bound to every order object; CI guard can be evaded by an allowlisted-file wrapper.

## J. Automated tests
`unittest discover`: **1,045 OK, 2 skipped**. Ruff clean. Mypy clean (78 files).
Secret scan clean. Execution bypass guard PASS. `paper_certification --no-broker`:
**67 PASS / 0 FAIL / 2 SKIP**. Preflight not treated as a pass in this worktree
(environment-dependent failures documented in the prior report). CI green on
the submitted branch.

## K. Git changes
Branch `feat/phase7-execution-auth`; new ADR, execution-path audit, this report,
six new modules, nine new test files, CI guard steps, viewer panel, orchestrator
and paper-flow hooks. Main checkout's staged/untracked files preserved.

## L. Remaining risks
1. ADR 0002 approved for design only; enforcement flag intentionally off until
   the remaining technical work below is complete.
2. `authorization_id` not propagated from intraday judge to each order; enabled mode
   would block buys rather than authorize them.
3. HMAC key has no rotation/key-id and is readable by same-user processes.
4. `decision_id` not recomputed at validation.
5. Split legacy kill switches not fully converged.
6. No live broker reconnect/locate verification.

## M. Final readiness verdict

**PAPER TRADING WITH RESTRICTIONS**

Unattended paper trading is not certified. ADR 0002 is approved (design scope).
Required before certification: complete order-object authorization propagation,
key management hardening, and a full 10:20 dry rehearsal with the flag enabled.
