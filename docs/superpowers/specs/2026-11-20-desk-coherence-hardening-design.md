# Desk Coherence and Hardening Design

## Intent

Make the paper-trading desk explain and enforce one consistent operating
model across its bots, pipeline documentation, web UI, reports, and health
checks. The system must remain paper-only, fail closed on missing evidence,
and never present stale or contradictory policy text as live truth.

## Current Problems

- `web/agent-office/pipeline.html` contains stale option-exit and risk/reward
  values.
- `scripts/desk_server.py` reads `outputs/paper/guard.json`, while the active
  options guard writes `outputs/paper/options_guard.json`.
- The UI has no shared policy/status payload, so displayed rules can drift
  from entry and exit code.
- Bot orchestration has portable interpreter support in some paths but still
  contains machine-specific defaults and inconsistent degradation reporting.
- The server documents loopback safety but has no explicit exposure guard or
  authentication boundary for non-loopback binds.

## Goals

1. Define one versioned paper-desk policy contract containing the active
   options IV cap, stop, modeled reward, minimum reward/risk, unlimited-winner
   behavior, VIX/regime requirements, and paper-only status.
2. Make bots and reports consume the same policy values rather than copying
   literals.
3. Make UI and pipeline views show the same contract and accurately surface
   guard, bot, data freshness, and fail-closed status.
4. Correct stale text and paths without changing exit availability or adding
   live-order capability.
5. Add tests for contract consistency, degraded inputs, path correctness,
   browser rendering, and non-loopback server refusal.
6. Push a verified commit to the configured remote after review.

## Non-goals

- No live trading or live broker enablement.
- No strategy optimization, new indicators, or sizing increases.
- No replacement of the stdlib web server or full frontend rewrite.
- No authentication system rollout without a separate security design;
  non-loopback binding will be refused by default rather than pretending the
  existing confirmation flag is authentication.

## Operating Contract

The shared contract will expose typed/constants data for:

- `paper_only = true`
- `quoted_iv_cap = 0.45`
- `guard_stop_pct = 0.25`
- `modeled_reward_pct = 0.50`
- `minimum_reward_risk = 2.0`
- `unlimited_winners = true`
- `max_vix = 25.0`
- SPY 50/200-day confirmation requirement
- canonical paths for `cio.db`, `options_guard.json`, and the paper report
- a policy version and generated status payload

The contract is read-only to UI consumers. Entry and exit code remains the
authority for enforcement; the contract prevents duplicated values and gives
the UI a truthful explanation of the rules.

Approved implementation decisions:

- The UI exposes both equity `guard.json` and options `options_guard.json`
  separately, normalized into one status payload.
- SPY 50/200 confirmation applies only to new options entries; missing
  confirmation blocks options but does not block research or reporting.
- Simulation overrides remain available. Paper/production entry paths may
  only tighten limits, never weaken them; any override is surfaced as a
  policy deviation.
- The pipeline remains static documentation, protected by a consistency test
  that fails when displayed policy labels drift.
- Freshness defaults are 5 minutes for market/broker data, 24 hours for guard
  and report artifacts, and 30 minutes for fleet artifacts. Stale entry data
  fails closed; stale display data is visibly degraded.
- A non-paper-safe runtime is `BLOCKED` and cannot reach an order path.
- The consumer scope includes all option entry/guard/report consumers,
  `desk_fleet.py`, `tradingagents_paper_auto.py`, `paper_position_guard.py`,
  and the UI, but not an unrelated rewrite of every repository script.

## System Changes

### Shared policy module

Add a small Python module under `robinhood_tools/` with constants, a
JSON-safe `policy_snapshot()` function, and canonical output paths. Existing
risk modules import values from it. Tests assert the options gate, IV
validator, guard defaults, and report use the same values.

### Bots and pipeline

- Replace stale literals and text in bot-facing reports and pipeline docs.
- Ensure fleet/task mode reports policy version, paper-only mode, source data
  freshness, and blocked/degraded reasons.
- Preserve task-mode research-only behavior and existing paper execution
  gates.
- Make interpreter selection portable and fail closed with an actionable
  status rather than silently using a missing machine path.

### Web UI and server

- Add a policy/status endpoint backed by the shared contract and current paper
  report/guard state.
- Correct guard filename usage and show source timestamps plus unavailable
  evidence explicitly.
- Add a compact policy card to the overview/positions view.
- Update pipeline diagram labels to the active policy.
- Refuse non-loopback binds unless an explicit future authenticated mode is
  implemented; retain security headers and add no unauthenticated remote
  control surface.

### Verification

- Unit tests for shared contract and all consumers.
- Server tests for policy endpoint, guard path, and bind safety.
- Playwright smoke tests for overview, positions, pipeline, stale-data/error
  states, and mobile layout.
- Existing full Python suite, scenarios, preflight, compilation, secret scan,
  and `git diff --check`.
- Review the diff, commit only intended files, push to the configured remote,
  and report the commit/remote result.

## Alternatives Considered

1. **Shared contract plus targeted fixes (selected):** smallest change that
   removes semantic drift while preserving the existing UI/server and paper
   controls.
2. **UI-first redesign:** improves presentation before semantics; rejected
   because it could make stale policy look more authoritative.
3. **Full platform rewrite:** rejected because it expands security and trading
   regression risk without evidence the current architecture cannot support
   the required contract.

## Success Criteria

- Every displayed policy value matches the shared contract and active gate.
- Guard state is visible from the canonical file.
- Non-loopback server startup fails safely and clearly.
- Bots report the same policy version and paper-only state.
- Browser smoke tests pass at desktop and mobile sizes.
- Full verification passes, no live calls are introduced, and the verified
  commit is pushed successfully.
