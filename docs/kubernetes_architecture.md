# Kubernetes Architecture — target, not yet built

Today: macOS launchd examples in `deploy/`, Codex automations, localhost
servers (`127.0.0.1` only). Nothing here changes that. This file specifies
where the system goes when a single host stops being enough, with
duplicate-order safety as the primary design driver.

## Service layout (start consolidated)

```text
namespace: trading
trading-ui ── trading-api (FastAPI, SSE) ── postgres / redis
orchestrator ── agent-worker (market, news, macro, scout, bull, bear, red, judge)
risk-engine ── execution-service ── position-manager
market-data-service ── backtest-worker ── notification-service
```

Begin with few Deployments (`api`, `agent-worker`, `risk+execution`,
`position-manager`, `postgres`, `redis`); split only for independent
scaling or blast-radius reasons. Microservice count is not a goal.

## Duplicate-trade protection (the critical section)

- Ledger unique constraints on `(order_fingerprint)` and idempotency keys;
  `reserve_execution` in a single transaction (Postgres `SELECT … FOR
  UPDATE` / advisory lock per symbol).
- One leader places per symbol: Redis Redlock/lease or K8s Lease election
  in the orchestrator; followers research but cannot submit.
- Crash recovery: on start, execution-service lists `executing`/
  `submitted` authorizations, reconciles each against the broker by client
  key, and only then resumes. Restarts are routine; double-submits are not.
- PodDisruptionBudgets on execution/position-manager; `Recreate`-safe
  design (no in-memory placement state anywhere).

## Config, secrets, policy

- ConfigMaps: strategy params, risk limits, session windows, universe
  sources. Changes via git → review → rollout (never via model output).
- Secrets: External Secrets Operator / cloud secret manager → K8s
  Secrets, mounted **only** to execution-service. Agent workers,
  UI, and backtest pods get zero broker credentials (enforced by
  NetworkPolicy + RBAC + distinct ServiceAccounts).
- Execution policy (`OBSERVE`/`APPROVAL`/`AUTONOMOUS`) and
  `LIVE_TRADING` are operator-owned values; the API rejects any change
  request originating from agent workloads. Default on fresh install:
  `SIMULATION` + `OBSERVE`.

## Observability and health

Prometheus metrics (`trade_candidates_total`, `orders_submitted_total`,
`orders_rejected_total`, `risk_rejections_total`, `agent_latency_seconds`,
`llm_failures_total`, `broker_failures_total`, `strategy_pnl`, `daily_pnl`,
`win_rate`, `profit_factor`, `max_drawdown`, `expectancy`), OpenTelemetry
traces per trade timeline, structured JSON logs without secrets. Health
gates (`Robinhood`, `market data`, `LLM`, `DB`, `agents`, `position
monitor`) feed the UI system strip; any required dependency DOWN means
no new trades. Health/readiness probes on every Deployment; resource
requests/limits from load-tested numbers, not guesses.

## Migration order (no big bang)

1. Dockerfiles + compose for local parity (simulation default).
2. Helm chart to a local cluster; Postgres + ledger migration with
   dual-write + verification job.
3. Staging namespace running shadow mode against paper brokers.
4. Production namespace only after shadow evidence + owner sign-off.
