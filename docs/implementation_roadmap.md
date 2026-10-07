# Implementation Roadmap — small phases, evidence-gated

Conventions: each phase is independently reviewable, ends with tests +
a review by a second agent, and reports results before the next begins.
Trading behavior changes only from Phase 4 on, and only in simulation.

## Phase 1 — Audit + architecture (this set of docs, in progress)

Deliverables: `current_system.md`, `dependency_map.md`, `architecture.md`,
`risk_model.md`, `trade_lifecycle.md`, `options_architecture.md`,
`robinhood_integration.md`, `kubernetes_architecture.md`,
`system_summary.md` update. No trading changes. Tests stay green (128 OK).

## Phase 2 — Domain, broker abstraction, risk engine (~4–6 PRs)

1. `Broker` Protocol + `SimulationBroker` (equity fills; options-ready
   types) using the existing `paper.py` simulator as seed.
2. `RiskEngine.check()` facade over current `risk/safety/policy/universe`
   checks (no semantics change; every rule keeps its test).
3. Options data types (contract, quote, Greeks) + chain-quality gates.
4. SQLite schema additions for candidate/debate/contract/risk-decision
   tables (backward-compatible migrations, backup first).
5. Converge scripts onto one executor (fix debt item 1); shared
   `universe.fetch` helper (fix debt item 2).

## Phase 3 — Multi-agent analysis, no orders (~3 PRs)

Typed agent messages (market/news/macro/scout/bull/bear/red/judge) running
against recorded fixtures + live read-only data; full debate + judge +
risk verdict persisted; execution policy forced `OBSERVE`. Success =
`NO_TRADE` rate and agreement calibration reported, not trade count.

## Phase 4 — Options simulator season (~2–3 PRs)

Simulator gains long-option pricing/fills/expiry; strategies run end-to-end
in `SIMULATION`; simulator fill logic property-tested. No broker contact.

## Phase 5 — Console UI (~3–4 PRs)

FastAPI + SSE backend over the ledger; desk view (decision + confidence +
contract + risk + WHY), debate view, position view with Greeks, autonomy
control (OBSERVE/APPROVAL/AUTONOMOUS + kill switch), health strip, trade
timelines. Replaces static HTML only when parity is demonstrated.

## Phase 6 — Backtest/incubation/scorecard (~2–3 PRs)

Promote `strategy_backtest.py` patterns into versioned experiments with
the `EXPERIMENTAL→…→ACTIVE` pipeline, quarantine on sim/shadow divergence,
and the full scorecard (win rate, profit factor, Sharpe/Sortino, drawdown,
splits by ticker/weekday/hour/regime/confidence/model/version).

## Phase 7 — Robinhood read-only (~2 PRs)

Chains, quotes, positions, orders through `RobinhoodBroker`; chain-quality
breaker; no review/place paths.

## Phase 8 — Review-only options (~2 PRs)

Ledger authorizations that cannot execute; proves fingerprinting and
idempotency against real review responses.

## Phase 9 — Human-approval execution (~2 PRs + drills)

Single-submit + reconcile with `require_human_confirmation`; timeout,
duplicate-webhook, restart-during-submit, and kill-switch drills green.

## Phase 10 — Shadow autonomous (~1 PR + waiting period)

Withheld orders, scored outcomes vs proposals. Promotion needs the
evidence file, not elapsed time.

## Phase 11 — Autonomous config (owner-gated, no PR auto-merges)

Caps proportional to evidence; explicit human enablement; default stays
`OBSERVE`. Reversible in one action (kill switch).

## Decisions needed from the owner (before Phase 2)

1. Authorize the equity→options policy change (or scope Phase 2+ to
   equities only). **Nothing options-related starts without this.**
2. Confirm conservative risk defaults (`risk_model.md` numbers).
3. Confirm Postgres/Redis + Kubernetes as the target (vs. staying on
   SQLite/launchd longer).
4. Confirm LLM providers + per-agent model budget (Kimi working today;
   OpenAI/Anthropic keys are owner-supplied, never committed).
