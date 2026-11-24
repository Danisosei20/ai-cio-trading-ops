# Phase 7.4 — Operational Security & Certification Report

Date: 2026-11-24. Baseline `be7e7ae`. ADR 0001/0002 preserved. Enforcement OFF.

## A. Executive summary
Fixed deterministic audit-chain verification across key rotation, added an
explicit administrative bootstrap, hardened close-order verification against
exposure reversal, and removed the unkeyed-MAC audit forgery path found in
adversarial review. Real Alpaca paper-broker rehearsal and Windows ACL
verification remain NOT TESTED because explicit authorization/artifacts were
absent. Verdict: PAPER TRADING WITH RESTRICTIONS.

## B. Repository baseline
Verified `be7e7ae`; user's staged/untracked files intact. Test baseline 1,138 OK.

## C. Audit-chain key rotation
Archival key policy implemented: retired/revoked keys retain verification
material for historical audit rows but cannot sign; only ACTIVE keys sign.
`verify_chain()` binds each row to its own `kid`, rejects duplicate event IDs,
reordering, tampering and unknown-kid forged rows (unkeyed MAC removed).
`tests/test_audit_rotation.py` covers 1/2/3-key chains, retirement, tampering,
duplicates and compromised-key refusal. Residual: tail truncation has no
external anchor; real Windows process-restart verification NOT TESTED.

## D. First-run initialization
`scripts/auth_bootstrap.py initialize --root <repo> --admin-confirm INITIALIZE-AUTH-STORE`
creates/verifies keystore, audit chain and DB, refuses overwrite of a valid
store, rejects unsafe paths, writes a redacted report, leaves enforcement OFF
and submits no orders. Tests cover initialization, repeat refusal and missing
confirmation.

## E. Windows security
See `windows-operational-acl-report.md`. Ten scheduled tasks run as one
Interactive/Limited identity. Keystore/DB/audit artifacts absent on host → real
ACL checks NOT TESTED. Code applies DPAPI/atomic writes/best-effort ACLs only.

## F. Signing authority
See `signing-authority-isolation-decision.md`. Current single-user model
accepted with restrictions. Separate signer identity recommended but not
implemented (requires owner approval). Same-user compromise can undermine the
boundary; no claim otherwise.

## G. Broker-verified exits
`close_order_guard.validate_close_order()` rejects oversized closes, wrong
direction/instrument, missing broker positions and unknown effects. Wired into
`paper_flow` under enforcement using broker-reconciled positions where
available. With enforcement OFF (operational default) exits are unchanged, so
live stop/flatten behavior is preserved. Real broker verification NOT TESTED.

## H. Full 12-stage replay
Two controlled replays were executed with frozen session evidence. Without the
LLM interpreter configured, the gate correctly halted on a failed ResearchFleet
stage (12 reports, no order). A second replay with the documented interpreter
override was started; its verdict is recorded in `C:\Temp\p74-gate2.log`.
No stage checks were weakened.

## I. 10:20 Alpaca paper rehearsal
**NOT TESTED.** No explicit owner authorization for real paper orders was
available. Prior gateway/mock results are not broker-verified evidence.

## J. Options verification
Prior mock-only. Real contract lookup, OCC validation, quote freshness, spread,
OI/volume and options exposure checks are not covered by a broker rehearsal.
Fail-closed behavior exists in code; classification remains MOCKED/NOT TESTED.

## K. Fault injection
Covered at helper/harness level (uncertain intent, revoked parent, expired
child, kill switch, audit loss, tampering, duplicate ID, rotation). Dangerous
broker/OS failures were not injected against real infrastructure.

## L. Scheduler certification
Ten registered tasks inventoried; paths quoted via 8.3 short names, all Ready.
No task identity or permission changes made. DST/holiday recovery and overlap
prevention not operationally exercised.

## M. Independent adversarial review
HIGH: unkeyed-MAC audit forgery (fixed; regression test added); close guard
breaking exits (fixed by enforcement-scoped wiring with broker positions).
MEDIUM/LOW: documented as residual where not fixed (unauthenticated supplied
positions, bootstrapping literal confirmation, docs clarified).

## N. Automated testing
**1,156 OK, 2 skipped**; ruff clean; mypy clean (83 files); secret scan clean;
bypass guard PASS; certification harness 67 PASS / 0 FAIL / 2 SKIP; CI green.
Windows ACL and real broker checks NOT TESTED — not counted as passes.

## O. Git changes
Branch `feat/phase7-4-operational`; commit and PR to be recorded after merge.
User's 10 staged and untracked files preserved.

## P. Remaining blockers
1. Explicit owner authorization for a real Alpaca paper rehearsal.
2. Windows ACL verification in an initialized environment.
3. Dedicated signer identity / owner acceptance of single-user risk.
4. Real broker options and contract validation rehearsal.
5. Full 10:20 chain with real broker reconciliation.

## Q. Final certification verdict

**PAPER TRADING WITH RESTRICTIONS**

Technical hardening is verified in code and automated tests, but unattended
paper trading is not certified: no real broker rehearsal, no Windows ACL
evidence, and a shared-identity signer trust boundary remain. Enforcement must
stay OFF. Owner approval is required before activation; passing tests do not
grant operational permission.
