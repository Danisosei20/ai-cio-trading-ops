# Architecture — current and target

## Current architecture (as built)

```text
                    .env + config/approval_routes.json
                        │ (strict schema, fail-closed)
                        ▼
              runtime.build_settings()
                 mode / kill switches / caps
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   research_only    paper_auto     live_approval
   (no service)    Alpaca paper   Robinhood live
                   + ledger       + human confirm
```

Paper placement path (only path that can move money, and only paper money):

```text
signal (LLM debate / webhook alert)
  → universe gate (S&P500 ≤24h)
  → data gates (price, earnings blackout, freshness)
  → risk gates (caps, buying power, shape: limit/DAY/regular/no-chase)
  → service.review_equity_order()      (broker review, no order)
  → CioDatabase.create → approve()     (durable, expiring, fingerprinted)
  → execution_guard (session window + market clock)
  → reserve_execution()                (approved→executing, atomic)
  → backend.place_equity_order()       (fingerprint-bound review ID)
  → mark_executed()  |  mark_reconciliation_required()
```

Observation paths (never place): `daily-review`, watchdog, dashboards,
backtests, research bridge.

## Target architecture (options desk)

Keep the spine above unchanged. Add lanes around it; never bypass it:

```text
                      Watchtower console (Next.js + FastAPI, SSE)
                                     │
                              Orchestrator (state machine)
            ┌────────────┬───────────┼───────────┬────────────┐
            ▼            ▼           ▼           ▼            ▼
     Market agent  News agent  Macro agent  Options scout  Bull/Bear/Red
     (rules+LLM)   (rules+LLM) (rules+LLM)  (chain math)   (LLM debate)
            └────────────┴───────────┼───────────┴────────────┘
                                     ▼
                              Strategy judge (LLM, NO_TRADE allowed)
                                     │  typed proposal only
                                     ▼
                     Deterministic risk engine (code, NOT LLM) ── veto
                                     │  approve → signed authorization
                                     ▼
                        Broker interface (one interface)
                        ┌────────────┼────────────┐
                        ▼            ▼            ▼
                   Simulation   Alpaca paper   Robinhood
                   (dev/test)   (incubation)   (execution, gated)
                                     │
                                     ▼
                        Position manager (HOLD/TRIM/CLOSE)
                                     │
                                     ▼
                        Ledger + audit + scorecard (Postgres later)
```

Rules that survive the migration (non-negotiable):

1. LLM proposes; deterministic risk disposes. No bypass path in code.
2. No LLM, agent, or webhook can change `LIVE_TRADING` / execution policy.
   Policy changes are config + human, never model output.
3. Every order needs review → ledger authorization → fingerprint match →
   idempotent submit → reconcile. Same five steps on every broker.
4. `NO_TRADE` is a successful outcome; volume of trades is not a metric.
5. Replicas never duplicate orders: ledger unique constraints +
   idempotency keys + atomic reserve (SQLite `BEGIN IMMEDIATE` today,
   Postgres + advisory locks later).
6. Secrets flow one way: execution service ← secret store. Agents never
   see credentials; prompts/logs/UI are sanitized.

## What stays / refactors / gets replaced

- **Stays:** `service.py` guard pattern, `database.py` approval machine
  (extended, not replaced), `alpaca_paper.py` URL pinning, config
  fail-closed validation, reconciliation discipline.
- **Refactors:** paper scripts converge onto one executor (see debt item 1
  in `docs/dependency_map.md`); `Sp500Snapshot` gains a shared fetcher;
  dashboard splits live-fetch from static render.
- **Replaced over time:** SQLite → Postgres (ledger), file/JSON saliva →
  typed agent messages, launchd → Kubernetes, static HTML → console UI.
  Each replacement is its own phase with dual-run + rollback criteria.
