# ADR 0002 — Deterministic Execution Authorization and Broker Revalidation

**Status: APPROVED by owner — 2026-11-23. Scope: design approval only.**
**Enforcement flag stays OFF** until order-object propagation, key hardening and
a full 10:20 rehearsal are complete. Approval of this ADR does NOT authorize
unattended entries or any live trading change.
**Date:** 2026-11-23
**Supersedes:** nothing; complements ADR 0001.

## Problem statement

The 09:45 deterministic gate produces `proceed`/`review`/`halt`, but the 10:20
order workflow calls `tradingagents_paper_auto.py` directly without consuming
that decision. Only `intraday_loop.py` reads `outputs/orchestrator/latest.json`.
A verdict is a point-in-time fact; nothing binds it to a specific order, account,
broker, size, or time window.

## Threat model

- A stale `proceed` from an earlier session authorizes a new order.
- A modified artifact or decision file is treated as genuine.
- Two workers consume the same authorization and double exposure.
- Account/broker mismatch sends a paper order to the wrong venue.
- A research model (or a prompt-injected artifact) influences an order.
- An expired or revoked approval is reused after a crash.
- A short is opened without broker short eligibility.

## Current shortcomings

See `docs/architecture/execution-path-audit.md`. In short: no decision identity
in `RunDecision`, no hashes, no expiry, no account binding, no atomic consumption,
four independent kill switches, drawdown measured from all-time peak with the
weekly limit unused, and no short-borrow validation.

## Proposed protocol

1. At 09:45, after a `proceed`, derive an **ExecutionAuthorization** from the
   immutable decision and the *validated artifacts* that produced it.
2. Canonical serialization (sorted keys, fixed separators, UTC timestamps) and
   SHA-256 artifact hashes bind evidence to the authorization. An HMAC over the
   canonical authorization body (key from the operator-controlled environment or
   a local key file) prevents untrusted modification. Missing key = fail closed.
3. Fields: authorization_id, decision_id, gate_run_id, strategy_id, symbol,
   instrument identity, position effect, side, max_quantity, max_notional,
   max_risk, broker, account_id, environment, artifact hashes, verdict, gate
   timestamp, issued_at, expires_at, state, policy_version, idempotency key.
4. States: `PENDING → VALIDATED → AUTHORIZED → CONSUMED`; terminal:
   `REJECTED`, `EXPIRED`, `REVOKED`, `INVALIDATED`. Terminal states are never
   reusable.
5. Atomic reservation via SQLite `BEGIN IMMEDIATE` with a single-winner
   `UPDATE ... WHERE state='AUTHORIZED'`; the broker client_order_id is the
   authorization's idempotency key so a timeout cannot create a second fill.
6. At execution: verify HMAC, expiry, verdict, account, broker, symbol, side,
   quantity/notional/risk limits, then re-fetch broker state (buying power,
   positions, pending orders, market data freshness, kill switch, drawdown,
   event blackout, short eligibility) before reserving and submitting.
7. A failed 10:20 check invalidates the authorization; it never falls back to a
   synthesized `proceed`.

## Exit-order exemption

Stops, take-profits, buy-to-cover, EOD flatten and emergency liquidation are
risk-*reducing* and must not require a fresh entry authorization. They remain
authenticated, broker-reviewed, ledgered and reconciled. They are not exempt from
the unified kill switch's *entry* check because that check is entry-scoped only.

## Evidence integrity

Versioned canonical JSON + SHA-256 + HMAC. A plain JSON file alone is not proof
of authorization. The key is never committed.

## Concurrency / replay

Single-winner atomic reservation; durable idempotency key for the broker;
`CONSUMED` is terminal; replay of a consumed or terminal authorization is rejected
and audited.

## Alternatives considered

- Trust `latest.json` verdict alone — rejected (no binding, replayable).
- Use the existing per-order approval ledger alone — rejected (created at
  placement time, not derived from the 09:45 gate).
- LLM-generated authorization — rejected by ADR 0001.

## Migration

- Land infrastructure and tests with `EXECUTION_AUTHORIZATION_ENABLED=false` (fail closed).
- `intraday_loop` is the first consumer; other paths migrate behind the same flag.
- Owner approves this ADR and sets the flag to enable unattended entry.

## Rollback

Set the flag back to `false`. Existing manual entry paths continue to work;
authorization records remain auditable but are not enforced.

## Testing requirements

HMAC/tamper, hash mismatch, expiry, verdict halt, wrong account/broker/symbol/side,
over-limit quantity, replay, invalid transitions, concurrent consumers, uncertain
broker response, short eligibility failures, exit exemption, no-LLM assertion.

## Owner approval requirements

ADR adoption, enabling unattended entries, and any live-trading change each require
explicit owner approval. This ADR was **approved by the owner on 2026-11-23** (design scope only); unattended
entry enforcement therefore stays disabled by default.
