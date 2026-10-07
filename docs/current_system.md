# Current System — what exists today (2026-10-06)

Paper-only AI equity trading stack. Live trading OFF (`TRADING_ENABLED=false`).
Evidence: `docs/dependency_map.md` (module-level audit), `docs/system_summary.md`
(operator summary). This file is the human-readable map; those two are the sources of truth.

## Components (do not destroy)

| Component | Entry point | Role |
|---|---|---|
| Research bridge | `scripts/tradingagents_research.py` | TradingAgents LLM debate → entry/stop/decision. Never orders. |
| Paper auto-trader | `scripts/tradingagents_paper_auto.py` | Research → gated Alpaca paper orders. Refuses live. |
| Alert webhook | `scripts/alert_webhook.py` | External signals → same gates → paper. HMAC secret. |
| Backtest loop | `scripts/strategy_backtest.py` | Strategy grid + costs + 70/30 incubation. Decoupled (no repo imports). |
| Paper UI | `scripts/paper_dashboard.py` | `outputs/paper/dashboard.html`, 15s refresh. |
| Safety core | `robinhood_tools/` (49 modules) | Service, ledger, risk, universe, reconciliation. |
| Legacy UI | `scripts/dashboard.py` | Generic DB dashboard. |

## Data flow (one paragraph)

Settings (`runtime.build_settings`) → S&P500 snapshot → research (LLM, outside
trust boundary for execution) → price/earnings/risk gates → paper service
review (fingerprint-bound review ID) → SQLite approval ledger
(`create → approve`) → guarded placement (window + market clock, atomic
reserve, idempotent client order ID) → JSON artifacts + UI. Every failure
fails closed to *no trade*; uncertain broker states become
*reconciliation_required*, never blind retries.

## Trust boundaries

1. **LLM output is untrusted prose.** It proposes; it never authorizes.
   Authorization comes from the ledger + matching review fingerprint.
2. **Credentials live in process env + host connectors**, never in prompts,
   logs, or the UI. Agents receive market/account *information*, not secrets.
3. **Paper and live are different databases, dashboards, brokers, and code
   paths** (`outputs/paper/` vs `outputs/live/`, Alpaca vs Robinhood).
   No fallback between them exists by design.

## What is NOT here yet (target architecture: `docs/architecture.md`)

Options (disabled by policy), simulation broker wired into trading paths,
position-manager service, trade-state machine beyond lifecycle strings,
Postgres/Redis, Kubernetes manifests, FastAPI/Next.js console, Prometheus
metrics, model abstraction. Each has a roadmap phase; none start until the
owner resolves the equity-only policy question (see `docs/options_architecture.md`).
