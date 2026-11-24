# Audit-key rotation validation

Implemented archival key policy:
- Keys retain verification material on `RETIRED`, `REVOKED`, `COMPROMISED`
  for **historical audit verification only**; only `ACTIVE` keys may sign.
- Audit events carry `kid`; verification resolves that key through the
  archival accessor and accepts any candidate MAC (current, archived, unkeyed
  legacy fallback).
- `verify_chain()` detects duplicate event IDs, reordered/deleted entries,
  tampering before/after rotation, and missing key material.
- Concurrency uses a cross-process lock file; emit flushes + fsync.

Verified by `tests/test_audit_rotation.py` (6 tests): one key, rotation and
retirement, three rotations, tampering after rotation, duplicate event IDs,
and compromised-key signing rejection. All pass deterministically.

Historical verification is distinct from current execution authority: a
revoked key can verify old audit rows but cannot sign or authorize new entries.
