# Desk Coherence and Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the paper desk's policy, bots, pipeline, UI, and website present and enforce one accurate operating model, then verify and push it.

**Architecture:** Add one deep policy/status module in `robinhood_tools/` that owns finalized constants, canonical paths, freshness thresholds, and a JSON-safe snapshot. Existing entry/exit consumers import it; the server adapts it into API payloads; static pipeline text is protected by consistency tests. Keep the stdlib server and existing paper-only execution seams.

**Tech Stack:** Python 3, stdlib HTTP server, vanilla HTML/JS/SVG, unittest, Playwright smoke tests, existing paper broker and SQLite ledger.

**Spec:** `docs/superpowers/specs/2026-11-20-desk-coherence-hardening-design.md`

## Global Constraints

- No live trading or live broker enablement.
- Exits remain available and are never blocked by entry gates.
- Paper/production entry paths may only tighten limits, never weaken them.
- SPY 50/200 confirmation applies only to new options entries.
- Stale entry data fails closed; stale display data is visibly degraded.
- Non-paper-safe runtime status is `BLOCKED` and cannot reach an order path.
- No full frontend/server rewrite.

## Review Focus

- Canonical guard mismatch: UI must show equity and options guard state separately.
- Policy drift: stale `+2%` and `1.5:1` labels must fail a consistency test.
- Override bypass: CLI/simulation overrides must not weaken paper entry policy.
- Missing/stale market, broker, guard, report, and fleet data must have explicit states.
- Non-loopback bind and paper/live configuration must fail safely.

---

### Task 1: Shared policy contract

**Files:**
- Create: `robinhood_tools/desk_policy.py`
- Modify: `robinhood_tools/option_entry_risk.py`
- Modify: `robinhood_tools/options_gate.py`
- Modify: `scripts/options_guard.py`
- Modify: `scripts/paper_session_report.py`
- Test: `tests/test_desk_policy.py`

**Interfaces:**
- `DeskPolicy` immutable values for IV `0.45`, stop `0.25`, modeled reward `0.50`, minimum R/R `2.0`, VIX `25.0`, paper-only, and freshness thresholds.
- `policy_snapshot() -> dict` returns JSON-safe policy values and canonical paths.
- `policy_status(...) -> dict` returns `READY`, `DEGRADED`, or `BLOCKED` with source timestamps and reasons.

- [ ] Write failing tests for exact constants, snapshot shape, canonical paths, freshness classification, and non-paper-safe blocking.
- [ ] Run focused tests and observe failure because the module is absent.
- [ ] Implement the contract and make existing consumers import its constants without changing current exit availability.
- [ ] Run focused tests and the option regression tests.

### Task 2: Align all relevant bot and report consumers

**Files:**
- Modify: `scripts/options_pilot.py`
- Modify: `scripts/options_watch_trade.py`
- Modify: `scripts/tradingagents_paper_auto.py`
- Modify: `scripts/paper_position_guard.py`
- Modify: `scripts/desk_fleet.py`
- Modify: `scripts/weekend_digest.py`
- Test: `tests/test_policy_consumers.py`

**Interfaces:**
- Every relevant bot emits `policy_version`, `paper_only`, and explicit blocked/degraded reasons.
- Fleet interpreter selection remains portable and reports missing TradingAgents dependencies without silently claiming success.

- [ ] Add failing tests proving policy metadata is present and weakened overrides are refused or marked as deviations.
- [ ] Run focused tests and observe failures.
- [ ] Wire consumers to the shared contract; preserve research-only task mode and paper execution gates.
- [ ] Add portable interpreter diagnostics and explicit status handling.
- [ ] Run bot, options, fleet, and safety tests.

### Task 3: Server/API safety and accurate status

**Files:**
- Modify: `scripts/desk_server.py`
- Test: `tests/test_desk_server.py`

**Interfaces:**
- Add `GET /api/policy` returning the policy snapshot, normalized equity/options guard state, paper-session report status, and freshness/degradation reasons.
- Refuse non-loopback binds with a clear error; retain loopback support and security headers.

- [ ] Add failing tests for both guard files, missing/stale artifacts, `/api/policy`, loopback binds, and non-loopback refusal.
- [ ] Run focused server tests and observe failures.
- [ ] Implement the normalized read-only endpoint and bind safety without adding remote control.
- [ ] Run server and security regression tests.

### Task 4: UI and pipeline coherence

**Files:**
- Modify: `scripts/desk_ui/index.html`
- Modify: `scripts/desk_ui/positions.html`
- Modify: `scripts/desk_ui/app.js`
- Modify: `web/agent-office/pipeline.html`
- Test: `tests/test_pipeline_policy_consistency.py`
- Create: `tests/browser/test_desk_ui.py`

**Interfaces:**
- UI renders a policy/status card with current policy version, paper-only state, both guards, freshness, and explicit unavailable evidence.
- Pipeline diagram displays the actual −25% guard, unlimited winners, 2:1 reward/risk, and options gates.

- [ ] Add failing static consistency tests for stale values and required active labels.
- [ ] Add browser smoke scenarios for overview, positions, pipeline, stale/degraded state, and mobile viewport.
- [ ] Update UI rendering with escaped values, loading/error/empty states, and canonical API data.
- [ ] Update pipeline labels and accessibility metadata.
- [ ] Run the static tests and Playwright smoke tests through the server helper.

### Task 5: End-to-end verification and push

**Files:**
- No additional source files expected.

- [ ] Run the full Python test suite, scenarios, preflight, secret scan, compilation, and `git diff --check`.
- [ ] Run the browser suite and confirm no live/order/notification side effects.
- [ ] Review the diff for unrelated workspace changes and stage only intended files.
- [ ] Commit with a descriptive message.
- [ ] Push to the configured remote and verify the pushed commit/hash.
