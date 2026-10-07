# Dependency Map — Phase-1 Audit Evidence

Source: independent codebase audit (2026-10-06) + test baseline (128 tests OK).
Parent: `docs/system_summary.md`. This file is evidence; interpretation lives in
`docs/architecture.md` and `docs/implementation_roadmap.md`.

## Module responsibilities (`robinhood_tools/`)

| Module | Responsibility | Sibling imports |
|---|---|---|
| `errors.py` | Error taxonomy (`PolicyViolation`, `ConnectorUnavailable`, `AuthorizationRequired`, …) | none (leaf) |
| `models.py` | Dataclasses: `Account`, `EquityOrderRequest`, `OptionOrderRequest`, `Order`, `OrderReview` | none (leaf) |
| `auth.py` | Scope/entitlement gate | `errors` |
| `policy.py` | Order-shape + account policy | `errors`, `models` |
| `client.py` | `RobinhoodBackend` Protocol (host-owned connector boundary) | `models` |
| `service.py` | Safety wrapper: authz + account + policy + review→approval→place | `auth`, `client`, `errors`, `models`, `universe`, `policy` |
| `approvals.py` | Fingerprint + `ApprovalRecord` + legacy `JsonApprovalStore` | `errors`, `models` |
| `database.py` | `CioDatabase` SQLite schema v6: approvals, audit, lifecycles, fills, deliveries, learning, drift, kill/cooldown | `approvals`, `errors`, `governance`, `models`, `research` |
| `runtime.py` | Settings, `build_settings`, `build_paper_service`, `build_live_service`, session/broker guards | `alpaca_paper`, `database`, `errors`, `risk`, `service`, `settings` |
| `settings.py` | `.env` + `config/approval_routes.json` loading, strict fail-closed schema | `errors` |
| `risk.py` | `PortfolioState` + `RiskLimits.validate_purchase` | `errors` |
| `safety.py` | Data-quality score, regime hurdles (90/93/97), earnings blackout | `errors` |
| `universe.py` | `Sp500Snapshot.require_current_member` (HTTPS, ≤24h) | `errors` |
| `market_calendar.py` | Official market-calendar open-day check | `errors` |
| `analysis.py` | `MarketSnapshot`, `TradeCandidate`, `ExitPlan` | `errors` |
| `lifecycle.py` | Exit evaluation (hold/trim/sell); never places orders | `errors` |
| `alpaca_paper.py` | Paper transport pinned to `https://paper-api.alpaca.markets` + equity-only backend | `approvals`, `errors`, `models`, `reconciliation` |
| `alpaca_market_data.py` | Alpaca data-API client | `errors` |
| `paper.py` | Deterministic in-memory simulator (tests only) | `models` |
| `paper_autonomy.py` | `PaperAutoExecutor` + entry-context validation, lifecycle, learning, notify | `analysis`, `database`, `errors`, `lifecycle`, `models`, `risk`, `runtime`, `safety`, `service` |
| `workflow.py` | Human-in-the-loop CIO orchestration | `analysis`, `database`, `errors`, `logging`, `lifecycle`, `models`, `risk`, `service` |
| `orchestrator.py` | Daily run claim, preflights, shadow recs, position monitoring | `daily_controls`, `database`, `errors`, `lifecycle`, `runtime` |
| `daily_controls.py` | Freshness manifests, broker drift, watchdog, daily notice | `database`, `errors` |
| `operations.py` | Recovery plan, order polling, reply-window monitor, cooldowns | `database`, `errors`, `models`, `reconciliation`, `slack_replies` |
| `reconciliation.py` | Broker fills vs ledger reconciliation | `database`, `models` |
| `governance.py` | Immutable `DecisionRecord` provenance | `errors` |
| `research.py` | `ResearchExperiment` paper-only promotion (≥10 obs) | via `database` |
| `replay.py` | Point-in-time replay validation | `errors` |
| `learning.py` | Learning-checkpoint completion | `errors` |
| `accounting.py` / `portfolio.py` | Settled-cash + tax-lot helpers | `errors`, `portfolio` |
| `observability.py` / `health.py` | Operational status + health evaluation | `database`, `errors` |
| `notifications.py` / `slack_web_api.py` / `slack_replies.py` | Delivery records, sender, safe reply parsing (Slack never approves) | `errors` |
| `adapters.py` | Host-adapter Protocols (connectors stay outside repo) | `analysis`, `risk`, `universe` |
| `mcp_backend.py` / `mcp_config.py` / `tools.py` | MCP Robinhood backend + tool table | `errors`, `models`, `mcp_config` |
| `cli.py` | `cio` CLI (daily-review, approvals, kill, bundles) | `database`, `privacy`, `runtime`, `settings`, `slack_replies` |
| `logging.py` / `privacy.py` / `migrations.py` | Structured logs, redacted bundles, schema migrations | `database` |

## Data flows

**Paper auto trade:** `tradingagents_paper_auto.main` → `build_settings`
(refuse if live enabled) → Wikipedia S&P500 (fail-closed <400) → TradingAgents
debate → yfinance price/earnings gates → $500 cap + buying-power →
`build_paper_service` review (S&P500 re-check, asset checks, fingerprint review
ID) → `CioDatabase.create → approve` → `place_equity_order` (execution guard:
window + clock; atomic `reserve_execution`; fingerprint-bound `client_order_id`)
→ `auto.json` + `status.json` + `live.log`.

**Approval lifecycle:** `pending → approved → executing → executed`, with
`expired`/`rejected`/`failed`/`reconciliation_required` exits. Atomic
`BEGIN IMMEDIATE` reservation prevents duplicates; broker exceptions mark
reconciliation-required (never blind retry).

**Dashboards:** `paper_dashboard.py` (Alpaca account/positions/orders +
`auto.json`/`status.json`/`live.log`/`latest.json` + approvals/lifecycles →
`outputs/paper/dashboard.html`, 15s refresh) and `dashboard.py` (generic DB view).

## Risk controls (enforcement points)

Live kill switch (`runtime.require_live_trading` + script refusals) · paper
session guard (`require_paper_execution` + clock) · emergency kill + symbol
cooldowns (`database`) · fingerprint binding (`approvals`/`database`/`service`/
`alpaca_paper`) · approval expiry · human confirmation (live always) ·
scope authz (`auth`) · explicit agentic-only account (`policy` + backends) ·
S&P500 ≤24h (`universe`) · earnings blackout 5d fail-closed (`safety`) ·
regime hurdles 90/93/97 · spread/ADV/weight/cash/loss limits (`risk`) ·
limit-DAY-regular-session-no-chase shape (`paper_autonomy`, `alpaca_paper`) ·
panic-entry hurdles · TradingView consistency · freshness ≤5min + timestamp
match ≤60s · broker-drift blocks · daily-run idempotence · one-lifecycle-per-
symbol + leases · Slack never approves · data-quality 100/100 · options
disabled · secret scan · strict config schema · reconciliation-required ·
webhook HMAC secret.

## Broker routing

`paper_broker=alpaca` + `live_broker=robinhood` pinned at startup.
`build_paper_service` (paper only, clock-guarded) vs `build_live_service`
(live only, human-confirm forced) vs research-only (no service). No fallback.

## Storage / config / deploy / tests

SQLite v6 (`outputs/paper/cio.db` in paper mode) + JSON artifacts
(`auto.json`, `status.json`, `live.log`, backtest runs, dashboards).
Config: `.env` (ignored) + `config/approval_routes.json` (strict schema).
Deploy: macOS launchd examples only — **no Dockerfiles, no Kubernetes
manifests**. Tests: 18 files in `tests/`, 128 passing, covering config
rejection, approval machine, service gates, Alpaca URL pin, autonomy gates,
lifecycle, drift, Slack safety, kill/cooldown, secrets, governance/replay.

## Technical debt (top 5)

1. Two paper-execution paths with different gate depth (`PaperAutoExecutor`
   full vs scripts inline subset) — drift risk.
2. Fragile Wikipedia S&P500 scrape duplicated in two scripts.
3. Dashboard hits live broker synchronously on every render.
4. Legacy `JsonApprovalStore` ships beside atomic `CioDatabase`.
5. Secrets + ledger on one host, chmod-600 convention only (fine for paper).
