# Security Policy

## Money first

This software can move real money through brokerage APIs. Treat every
security report as urgent.

- **Do not open public issues for credential leaks, auth bypasses, order
  injection, or anything that could move funds.** Report privately to the
  repository owner instead.
- If you believe a live key was exposed, rotate it at the broker
  immediately, then tell us what was exposed and where.

## Scope of protection

- Broker credentials must only exist in the execution service process
  (environment / secret manager). Agents, prompts, logs, dashboards, and
  the UI must never carry them.
- Order placement requires review + ledger authorization + fingerprint
  match + idempotency key. Reports that show a bypass are critical.
- The kill switch must always be able to stop new orders.

## Supported versions

Only `main` is supported. Security fixes land on `main` with a test that
reproduces the issue (redacted where sensitive).

## Response

We aim to acknowledge private reports within 72 hours and to ship a fix
with a regression test before any public disclosure.
