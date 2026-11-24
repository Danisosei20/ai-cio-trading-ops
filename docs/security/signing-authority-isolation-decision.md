# Signing authority isolation decision

## Current model (Option B — accepted with restrictions)
All desk processes run as one Windows identity. DPAPI is user-scoped; any
same-user process can unprotect the key, modify SQLite authorization state,
or reach broker adapters. HMAC gives integrity but not process isolation.

This model is retained because installing a privileged Windows service or
changing scheduled-task identities requires explicit owner approval and was
not performed.

## Recommended model (Option A — not implemented)
Dedicated least-privileged service identity holding an asymmetric private key;
trading workers hold only a public verification key; child derivation and
CONSUMED state transitions move behind authenticated local IPC with caller
principal checks, scope/expiry enforcement, replay cache, and bounded failure.
This requires an ADR amendment and owner approval before installation.

## Decision
Do not enable unattended enforcement until Option A is deployed (or the owner
explicitly accepts Option B residual risk in writing). No operational service
or identity changes were made.
