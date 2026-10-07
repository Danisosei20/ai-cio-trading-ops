# Trade Lifecycle — explicit states, persisted transitions

Every candidate and order moves through named states. Every transition is
written to the ledger before the action it authorizes. Crash recovery =
read the ledger, resume from the recorded state, reconcile with the broker
before retrying anything.

## Candidate/idea states

```text
DISCOVERED → RESEARCHING → DEBATING → OPTIONS_SCANNING → RISK_REVIEW
   → APPROVED | REJECTED
```

- `DISCOVERED`: signal arrived (debate output, webhook alert, backtest
  promotion). Record source + timestamp.
- `RESEARCHING`: market/news/macro agents attach evidence with provider +
  observation timestamps. Missing or stale evidence → `REJECTED` (reason:
  data quality), never assumed.
- `DEBATING`: bull/bear/red-team opinions recorded with scores. Forced
  consensus is a defect; disagreement is data for the judge.
- `OPTIONS_SCANNING`: scout attaches ranked contracts (or "no suitable
  contract" → `REJECTED`). Equity phase: order terms instead.
- `RISK_REVIEW`: deterministic engine verdict recorded rule-by-rule.
  FAIL → `REJECTED` with failed rules listed.

## Order states

```text
APPROVED → ORDER_PENDING → ORDER_SUBMITTED → PARTIALLY_FILLED → FILLED
   → MANAGING → EXIT_PENDING → CLOSED
              ↘ CANCELLED / ERROR / REJECTED
```

- `APPROVED`: ledger authorization exists (fingerprint + review ID +
  expiry). Nothing has touched the broker.
- `ORDER_PENDING → ORDER_SUBMITTED`: atomic reserve (`approved→executing`)
  then single submit with idempotency key. Pod crash here → on restart,
  reconcile broker orders by client key before any resubmit.
- `PARTIALLY_FILLED`: fills recorded per `(order_id, fill_id)`; position
  math uses weighted average price. No double-counting on redelivery.
- `FILLED → MANAGING`: lifecycle opens; position manager owns the exit
  (HOLD / TAKE_PARTIAL / MOVE_STOP / CLOSE / EMERGENCY_CLOSE).
- `EXIT_PENDING → CLOSED`: exits go through the same
  review → authorize → submit → reconcile path as entries.
- `CANCELLED`: explicit cancel with reason. `ERROR`: failed after
  reconciliation determined the true state. `REJECTED`: human/autonomy
  refusal with reason (a first-class, reportable outcome).

## Mapping to today's tables

`trade_lifecycles.status` (`research/buy_pending/open/sell_pending/closed/
rejected`) + `approvals.status` (`pending/approved/executing/executed/
expired/rejected/failed/reconciliation_required`) + `order_fills` already
implement this machine for equities. The options phase adds contract fields
and the SCANNING/RISK states — as new columns/tables, not by redefining
existing states (migrations stay backward compatible, backup first).

## Audit timeline (per trade, UI-visible)

`discovered → analyses complete → debate → scan → judge → risk PASS/FAIL →
reviewed → submitted → filled @ price → manage events → exit → outcome`.
Each line carries actor (agent/model/rule/human), code version, and broker
response. If a line is missing, the trade did not happen that way.
