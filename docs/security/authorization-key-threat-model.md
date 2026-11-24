# Authorization key threat model (Phase 7.2)

Date: 2026-11-23. Scope: ADR 0002 execution authorization signing.

## Trust boundary finding

All ten scheduled desk tasks and the viewer run as the same Windows identity
(`Danis Akoto`, Interactive/Limited). `_hmac_key()` currently reads one symmetric
key from either `EXECUTION_AUTH_HMAC_KEY` or `~/.cio_execution_auth.key`. Any
process running as that user can read the key, sign arbitrary authorizations,
edit the SQLite store, and modify broker requests. A research process driven by
prompt-injected content is inside this trust domain.

**DPAPI + ACL protects against a different local user and offline file theft.
It does NOT separate signing authority from order execution under the actual
scheduled-task architecture.** An HMAC signature does not establish independent
authorization when the verifier can also sign.

## Decision

The safest practical approach compatible with the current architecture:

1. Implement centralized, versioned key management with 256-bit random keys,
   DPAPI protection on Windows (with explicit ACL best-effort), key IDs,
   rotation states and a controlled CLI.
2. Implement durable revocation, decision-identity binding and tamper-evident
   audit logging so the documented fail-closed/replay claims become true.
3. **Keep enforcement OFF.** Record that genuine signer/verifier separation
   requires a dedicated least-privileged service identity with asymmetric keys
   (private key in signer, public verification key in consumers) and a bounded
   IPC surface. Until that exists, do not claim process isolation.

## Assets / threats

| Asset | Threat | Mitigation here |
|---|---|---|
| Signing key | raw key committed/logged/exposed | CSPRNG 256-bit, DPAPI at rest, no key material in records/logs/API, masked IDs |
| Signing key | file replacement/symlink/junction | atomic writes, no-follow checks, ACL best-effort, explicit store path |
| Signing authority | verifier signs arbitrary payloads | key IDs, purpose binding, algorithm pinning, operation type checked at gateway |
| Authorization | forged/modified payload | canonical versioned serialization, HMAC-SHA256, constant-time compare |
| Authorization | replay after restart | durable atomic single-use consumption, revocation store |
| Parent/child | scope expansion | parent bounds enforced, expiration capped, aggregate child cap |
| Decision | stale evidence authorizes new risk | decision digest recomputed from persisted decision, parent age checks |
| Revocation | bypass after rotation | revocation state checked at validation, persisted in SQLite |
| Audit | silent tampering | append-only JSONL with hash chain |

## Windows compatibility verification

Code-level compatibility reviewed on this host (runtime ACLs NOT TESTED) (Windows, Python 3.11):

- `KeyStore` selects `%LOCALAPPDATA%\ai-cio-trading-ops\authorization_keys.json`
  and uses `os.open(O_EXCL, 0o600)` + `os.replace` for atomic writes.
- `icacls` restricts the store to the current user and `SYSTEM` on a best-effort
  basis; the owner must verify ACLs on the target host with
  `icacls <store>` and confirm no inherited grants remain.
- DPAPI calls are Windows-only; non-Windows use requires the explicit
  `CIO_AUTH_ALLOW_PLAINTEXT_KEYSTORE=1` test mode. CI uses ephemeral keys only.
- SQLite uses WAL/`BEGIN IMMEDIATE` for reservation and revocation; concurrent
  worker and process-restart behavior is covered by unit tests.
- Key operations are CLI-driven (`scripts/auth_key_cli.py`); no scheduled task
  performs rotation or initialization automatically.

**Not proven by CI:** actual Windows ACL correctness, DPAPI behavior under a
different service identity, and same-user process isolation (impossible here).

Manual owner verification before any unattended enforcement:
1. `python scripts/auth_key_cli.py verify-store` → OK.
2. `icacls %LOCALAPPDATA%\ai-cio-trading-ops\authorization_keys.json` → owner + SYSTEM only.
3. Confirm no `EXECUTION_AUTH_HMAC_KEY` is set in process/user/machine env.
4. Confirm `EXECUTION_AUTHORIZATION_ENABLED` is unset or `false`.

## Residual risks (accepted, not fixed here)

- Same-user process isolation is impossible with a shared symmetric key.
- DPAPI is user-scoped; any same-user process can unprotect.
- Windows ACL hardening is applied best-effort and needs an owner-verified
  manual check on the production host.
- A separate signer service is required before unattended enforcement.
