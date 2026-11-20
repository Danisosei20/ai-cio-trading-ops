# Paper Autopilot Evidence and Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the paper options autopilot evidence-first, session-aware, duplicate-safe, and immediately observable through Slack.

**Architecture:** Add a focused evidence/lease module around the existing paper options flow. The scanner creates a deterministic technical snapshot, persists a candidate before invoking existing gates, and records the final order result. The alert adapter sends only after broker acceptance and retries independently; the existing guard remains the exit authority.

**Tech Stack:** Python 3.11, SQLite, atomic JSON files, existing Alpaca paper adapter, existing Slack notifier, pytest/unittest, exchange calendar/Alpaca clock.

**Spec:** `docs/superpowers/specs/2026-11-20-paper-autopilot-evidence-alerts-design.md`

## Global Constraints

- Paper trading only; no live broker enablement.
- Long calls and puts remain supported.
- Existing entry gates remain authoritative and entries fail closed.
- Exits remain available and are not disabled by entry logic.
- Evidence is recorded before every screened candidate entry attempt.
- Accepted paper orders emit immediate Slack alerts.
- Closed sessions, weekends, and holidays suppress periodic Slack checks.
- SQLite is the source of truth; atomic JSON evidence artifacts are also written.
- Duplicate prevention uses candidate fingerprint plus exact OCC identity and existing underlying blocks.
- Multiple autopilot processes are possible; a database lease prevents concurrent entry cycles.
- No guarantee of profit or win rate; measure expectancy, drawdown, and loss severity.

## Review Focus

- Missing/stale indicator or quote evidence must block entry and explain why.
- A Slack failure after broker acceptance must not cause a duplicate order.
- A second process must fail to acquire the lease and must not enter.
- Holiday and weekend checks must suppress periodic notifications without suppressing exit observation.
- An exact OCC duplicate must be blocked even when the scanner returns the same underlying again.

---

### Task 1: Evidence and lease module

**Files:**
- Create: `robinhood_tools/paper_autopilot.py`
- Test: `tests/test_paper_autopilot.py`

**Interfaces:**
- `build_evidence(candidate: dict, *, now: datetime) -> dict`
- `candidate_fingerprint(evidence: dict) -> str`
- `write_evidence(root: Path, evidence: dict) -> Path`
- `claim_cycle(db_path: Path, owner: str, now: datetime, ttl_seconds: int = 900) -> bool`
- `release_cycle(db_path: Path, owner: str) -> None`
- `record_attempt(db_path: Path, evidence: dict, action: str, reason: str, order: dict | None = None) -> None`

- [ ] Write failing tests for complete evidence fields, stable fingerprints, atomic JSON output, lease exclusivity/expiry, and pre-gate attempt persistence.
- [ ] Run `pytest tests/test_paper_autopilot.py -v` and observe the expected missing-module failure.
- [ ] Implement the minimal SQLite schema/migrations, atomic artifact write, canonical JSON normalization, and lease lifecycle.
- [ ] Run the focused tests and confirm they pass.
- [ ] Run `git diff --check`.

### Task 2: Session and market-hour policy

**Files:**
- Modify: `robinhood_tools/market_calendar.py` or the existing calendar utility identified during implementation
- Modify: `scripts/options_watch_trade.py`
- Test: `tests/test_paper_autopilot_schedule.py`

**Interfaces:**
- `entry_session_status(now: datetime, clock: dict | None, calendar: object | None) -> dict` returns `OPEN`, `CLOSED`, `WEEKEND`, or `HOLIDAY` with `can_enter` and `can_periodic_alert`.

- [ ] Write failing tests for regular closed hours, weekends, exchange holidays, open sessions, and exit-observation independence.
- [ ] Run focused tests and observe the failure.
- [ ] Centralize session decisions using the existing exchange calendar/Alpaca clock; remove weekday-only notification shortcuts.
- [ ] Ensure periodic checks return quietly outside sessions while guard/exit observation remains callable.
- [ ] Run schedule and existing options-watch tests.

### Task 3: Technical evidence collection

**Files:**
- Modify: `scripts/options_watch_trade.py`
- Modify: `scripts/options_pilot.py`
- Modify: existing zone/indicator helper only where needed
- Test: `tests/test_paper_autopilot_evidence.py`

**Interfaces:**
- `collect_entry_evidence(symbol: str, side: str, quote: dict, analysis: dict, *, now: datetime) -> dict`
  returns the complete pre-entry snapshot consumed by Task 1.

- [ ] Write failing tests proving VWAP, EMA, POC, supply/demand, support/resistance, regime, IV, liquidity, earnings, freshness, and policy version are present.
- [ ] Add tests proving missing/stale evidence produces a blocked attempt and no broker call.
- [ ] Implement deterministic collection by adapting existing scanner/zone outputs; do not add LLM judgment to the entry decision.
- [ ] Persist screened candidates and downstream gate failures before invoking the gated order function.
- [ ] Run focused evidence, options-pilot, and options-watch tests.

### Task 4: Duplicate-safe paper order orchestration

**Files:**
- Modify: `scripts/options_watch_trade.py`
- Modify: `scripts/options_pilot.py`
- Modify: `robinhood_tools/options_flow.py` only if alert/result hooks require it
- Test: `tests/test_paper_autopilot_orders.py`

**Interfaces:**
- `run_paper_entry(evidence: dict, *, execute: bool, owner: str) -> dict` records before gating, respects lease/fingerprints, and returns action/reason/order metadata.

- [ ] Write failing tests for exact OCC duplicates, candidate fingerprint duplicates, existing held/open-order blocks, lease contention, and paper-only refusal.
- [ ] Verify the tests fail before implementation.
- [ ] Route accepted entries through the existing `place_long_option`/paper review and ledger flow; never bypass gates.
- [ ] Record accepted/rejected outcomes and release the lease in `finally` handling.
- [ ] Run order, options-flow, and safety regression tests.

### Task 5: Immediate Slack alert delivery

**Files:**
- Modify: existing Slack notifier module used by the options flow
- Modify: `scripts/options_watch_trade.py`
- Test: `tests/test_paper_autopilot_alerts.py`

**Interfaces:**
- `send_entry_alert(result: dict, *, notifier, clock, retry_until_seconds: int = 600) -> dict`
  returns `sent`, `failed`, `attempts`, `last_error`, and `sent_at` without placing orders.

- [ ] Write failing tests for complete accepted-order payloads, immediate send, bounded retry/backoff, and no duplicate order on alert failure.
- [ ] Implement alert delivery after broker acceptance only; persist delivery status in SQLite and the evidence JSON.
- [ ] Keep blocked attempts local/structured unless a high-severity operational alert is explicitly configured.
- [ ] Run alert and existing Slack tests.

### Task 6: End-to-end verification and delivery

**Files:**
- Modify: `docs/superpowers/specs/2026-11-20-paper-autopilot-evidence-alerts-design.md` only if implementation decisions need clarification
- Add/modify: targeted tests only

- [ ] Run focused autopilot tests and the complete `.venv\Scripts\python.exe -m pytest -q` suite.
- [ ] Run compilation, secret scan, scenario matrix, and `git diff --check`.
- [ ] Run a dry-run paper session proving no live calls and inspect evidence/alert artifacts.
- [ ] Review the diff for unrelated changes and stage only intended files.
- [ ] Commit, push, and create a PR against `main`; do not merge without explicit approval.
