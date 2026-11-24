# Windows signing-boundary verification (Phase 7.3)

## Tested environment
- Windows host identity: `danizpc\danis akoto`.
- All 10 `AI-*` scheduled tasks: principal `Danis Akoto`, `Interactive`,
  `RunLevel=Limited`, all `Ready`. No dedicated service identity.
- Default keystore `%LOCALAPPDATA%\ai-cio-trading-ops\authorization_keys.json`:
  **NOT PRESENT** on this host.
- Authorization SQLite store and audit log under
  `outputs/paper/`: **NOT PRESENT** in the live checkout at verification time.
- Enforcement flag: unset in process/user/machine env (OFF).

## Trust boundary
```
[one Windows identity: Danis Akoto]
  orchestrator (signs parent) ─┐
  intraday loop (derives)     ├─ shared HMAC key / DPAPI-protected at rest
  daytrade engine (derives)   │
  options flow (derives)      │
  equity gateway (consumes)   ─┘
  viewer (read-only telemetry)
  research stations (no signing API, same OS identity)
```
All components are inside one trust domain.

## ACL verification
Keystore/DB/audit ACLs could not be verified because those artifacts are not
present on this host. The code applies:
- `os.open(O_WRONLY|O_CREAT|O_EXCL, 0o600)` + `os.replace` for atomic writes;
- symlink/junction refusal for keystore path and parent;
- best-effort `icacls /inheritance:r /grant:r <user>:(F) SYSTEM:(F)`;
- DPAPI user-scoped protection on Windows.

**ACL correctness is NOT TESTED** until an owner initializes the keystore and
runs `icacls` manually. Linux CI is not evidence of Windows ACL behavior.

## Option B — existing single-user trust model (current)
Documentation explicitly states same-user compromise is outside the isolation
boundary. Practical protections: other local users cannot read DPAPI material
or ACL-restricted files; untrusted research/viewer inputs have no supported
signing API. Residual: any same-user process can unprotect DPAPI, edit SQLite,
or call broker APIs directly.

## Option A — separate signing authority (recommended for unattended)
A dedicated least-privileged Windows service identity holding an asymmetric
private key; consumers hold only the public verification key; child derivation
and CONSUMED re-signing move behind authenticated IPC. This requires explicit
owner approval to install a service or alter scheduled-task identities — not
performed here.

## Required owner actions
1. Initialize keystore via `python scripts/auth_bootstrap.py initialize --root <repo> --admin-confirm INITIALIZE-AUTH-STORE`.
2. Verify `icacls` shows only owner + SYSTEM.
3. Confirm no `EXECUTION_AUTH_HMAC_KEY` in process/user/machine env.
4. Approve Option A before unattended enforcement.

## Remaining vulnerabilities
Same-user process isolation, DPAPI user scope, unverified ACLs, CLI relies on
OS access control.
