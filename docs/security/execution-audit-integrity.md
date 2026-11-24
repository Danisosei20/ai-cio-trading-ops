# Execution audit integrity (Phase 7.3)

## Problem
Phase 7.2 audit emission was wrapped in `except Exception: pass`, so an
unavailable/corrupt audit store did not block a risk-increasing entry.

## Implemented controls
`robinhood_tools/execution_audit_gate.py`:

- `audit_health(root)`: requires the append-only JSONL audit file to exist, to
  pass its keyed hash-chain verification, and for its directory to be writable.
- `paper_flow.require_order_authorization`: when enforcement is enabled and the
  order has `position_effect` open/increase, audit health is checked **before**
  gateway validation. Unhealthy audit → structured rejection and an alert to
  `outputs/paper/authorization_audit_alerts.jsonl`. No fictitious success event
  is written to the main chain.
- Exits (`reduce`/`close`) and reconciliation are exempt from this gate.
- `submission_intents` SQLite state machine: `INTENDED → SUBMITTED → UNCERTAIN →
  RECONCILED_*`, so an uncertain broker response must be reconciled before a
  retry. Client order IDs remain broker-derived; the intent is durable.

## Event coverage
Authorization created/validated/consumed/revoked, key activated/rotated/
compromised, signature failures, entry blocked by audit, and submission intent
transitions. Account identifiers are masked; key/signature material is dropped.

## Crash-consistency matrix
| Failure point | Behavior |
|---|---|
| Before creation | no authorization; audit may be unavailable but no entry |
| During persistence | atomic SQLite insert; no partial record |
| Reserved, before broker | durable INTENDED; replay of consumed child blocked |
| Broker accepts, ack lost | UNCERTAIN state; retry requires reconciliation |
| Audit persistence | entry blocked before intent if store unhealthy |
| Partial fill | RECONCILED_* recorded; reservation not released early |
| Process restart | SQLite state survives; pending intents listed |
| Key rotation | old key VERIFY_ONLY; new key signs; audit MAC follows active key |
| Revocation | checked at validation and inside reservation transaction |

## Limitation
Audit append is not atomic with an external broker API call. The durable intent
state machine closes that gap operationally. Audit emit failure itself remains
fail-open for already-started operations, but a new entry cannot start when the
store is unhealthy.
