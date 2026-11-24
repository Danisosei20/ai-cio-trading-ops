# Phase 7.2 — HMAC Security, Rotation & Revocation Report

Date: 2026-11-24. Baseline: `c73daf6`. ADR 0001 preserved; ADR 0002 approved
(design scope). Enforcement flag remains OFF.

## A. Executive summary
Audited the authorization signing architecture, documented the trust model, and
implemented centralized 256-bit key management, key IDs, bounded rotation,
durable revocation (including parent cascade and global entry revocation),
decision-identity binding, purpose/algorithm pinning, a keyed audit hash chain,
security telemetry and an operational CLI. An adversarial review found three
HIGH and several MEDIUM issues; all HIGH and the substantive MEDIUM items were
fixed and retested. Same-user process isolation remains impossible without a
dedicated signer service.

## B. Security architecture
Trust boundaries are documented in `docs/security/authorization-key-threat-model.md`.
All desk tasks run as one Windows identity; DPAPI+ACL protects secrets at rest
but does not separate signing from execution. Signing authority remains in the
deterministic gate and its derived child authorizations. Research stations, the
viewer and broker adapters cannot mint authorizations through the supported API.

## C. Key storage
`robinhood_tools/authorization_keys.py`: CSPRNG 256-bit keys, Windows DPAPI
protection with best-effort `icacls` owner+SYSTEM restriction, atomic
`O_EXCL`/`os.replace` persistence, symlink/junction refusal, keyed store
integrity, explicit plaintext mode only for tests/CI, no key material in
records, status, logs or dashboard. Default store:
`%LOCALAPPDATA%\ai-cio-trading-ops\authorization_keys.json`.

## D. Key rotation
States: PENDING → ACTIVE → VERIFY_ONLY → RETIRED, plus REVOKED/COMPROMISED.
Only ACTIVE keys sign; VERIFY_ONLY keys verify within a bounded window.
Revoked/compromised keys are zeroized and cannot verify or sign, and cannot be
reactivated. CLI: `status|initialize|rotate|revoke|verify-store|audit-verify`
plus authorization revocation commands. No deployment-time automatic rotation.

## E. Parent/child security
Children inherit decision identity and evidence and are bounded by parent
quantity/notional/risk, a 20-child cap, expiration capping, and revocation
checks at derivation and consumption. Decision digests are bound in the signed
payload. Child creation from a revoked parent is rejected.

## F. Revocation
Single authorization, parent cascade, by key, by strategy/account via SQLite,
and emergency global entry revocation. Revocation state is checked inside the
atomic reservation transaction and survives restarts/rotation. Exits remain
operational because entry gating keys on `position_effect`.

## G. Replay protection
HMAC signature + kid/algorithm/purpose pinning, durable atomic single-winner
consumption, revocation checks, idempotency key, audit chain, concurrent-worker
and restart tests.

## H. Execution gateway security
The broker boundary rejects missing, expired, tampered, revoked, mismatched and
replayed authorizations for long, short and option entries; protective exits
remain exempt. The CI bypass guard passes.

## I. Adversarial review
HIGH: ephemeral key usable under enforcement (fixed: requires explicit
test-only override + 16-byte minimum); forgeable keystore checksum (fixed:
keyed HMAC + material zeroization); VERIFY_ONLY key could sign (fixed: ACTIVE
only). MEDIUM: sibling over-blocking (fixed), missing parent revocation checks
(fixed), unkeyed/non-locked audit (fixed: keyed chain + cross-process lock),
missing CLI revocation (fixed), dashboard account exposure (fixed: masked).
Residual: audit emit is fail-open by design; CLI has no OS-level authentication;
same-user isolation remains impossible.

## J. Testing
`unittest discover`: **1,117 OK, 2 skipped**. Ruff clean. Mypy clean (81 files).
Secret scan clean. Bypass guard PASS. Certification harness **67 PASS / 0 FAIL /
2 SKIP**. CI green on the submitted branch. Preflight was not used as a pass
gate (environment-dependent failures documented previously).

## K. Git changes
Dedicated branch `feat/phase7-2-hmac-security`; implementation commit `de0e2cf`;
PR #71, merged into main as `8e303c0`.
New modules: authorization_keys, authorization_audit, auth_key_cli. New tests:
keys, revocation, audit, CLI, dashboard, review fixes. Updated: execution
authorization, propagation, dashboard, CI, .gitignore, threat model.
Main checkout's staged/untracked files preserved.

## L. Remaining risks
1. No signer/verifier process isolation under one Windows identity.
2. DPAPI is user-scoped; any same-user process can unprotect.
3. Windows ACL correctness requires owner manual verification.
4. Audit emit failures do not block operations.
5. CLI operations rely on filesystem/OS access control, not an independent credential.

## M. Readiness verdict

**HMAC SECURITY HARDENING WITH RESTRICTIONS**

Cryptographic and operational hardening is implemented and verified; the
architectural limitation (single trust domain) remains. This verdict concerns
Item 2 only and does not certify unattended paper trading. Phase 7.3 rehearsal
and a dedicated signer identity are required before unattended enforcement.
