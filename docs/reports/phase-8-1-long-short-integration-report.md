# Phase 8.1 — Long/Short Execution Integration, Latency & Intelligence Hardening

Prepared **12:30 ET 2026-11-24 while the market was open**. Afternoon
end-of-day tasks were not due; those results are marked **INCOMPLETE**. Paper
only, enforcement OFF, no paper or live order submitted. ADR 0001/0002 preserved.

## A. Executive summary

Fixed the market-regime interface mismatch from a clean checkout without
touching the owner's staged fix; added latency instrumentation, a bounded
worker helper, deterministic prefiltering and an evidence cache; connected
bearish research to the existing short-setup validators in **shadow-only**
mode; hardened VIX provenance; added an end-of-day lifecycle verifier and a
new read-only dashboard section; added 39 regression tests. Independent
adversarial review found three HIGH defects in the new code, all fixed and
re-tested.

No policy thresholds, RISK_OFF behavior, authorization flag, protective exits
or live permissions were changed. No order was placed.

## B. Repository baseline

- HEAD `bf3e735` (PR #76 merged), CI green at start.
- Main checkout retains 10 staged and untracked user files, including the
  staged `robinhood_tools/market_prices.py` and `tests/test_market_prices.py`.
  None were modified in this worktree.
- Baseline: 1,189 tests OK / 2 skipped; certification 67 PASS.

## C. Market-regime defect

**Root cause:** committed `market_regime(spy_closes)` has no
`require_long_term` parameter, but committed `tradingagents_paper_auto.py:80`
passed that keyword. The bare `except Exception` at `:631-632` converted the
`TypeError` into `regime = "UNKNOWN"`, and `regime_gate_passes` then blocked all
new risk. Only the owner's staged working-tree fix made live runs report
`RISK_OFF`.

**Fix:** new `robinhood_tools/regime_compat.py` supports both signatures and
never converts a programming error into a regime value. The legacy path
reproduces the documented strict semantics exactly (50-day
`[-51:-1]`, 200-day `[-201:-1]`, `<201 → UNKNOWN`, CHOP on equality). The
research script now imports `regime_for_options` from the shim; thresholds are
unchanged. Regressions cover both signatures, strict downgrade, short/stale
history, and clean-checkout caller behavior. The owner's staged fix remains
untouched; once committed, the shim delegates to it directly.

## D. Research latency

Measured 11/24 judge batch (10:45:16→11:40:08): nine *sequential* LLM
researches at 232–443 s per symbol accounted for 3,287 s of 3,296 s wall time
(99.7%). Desk cross-checks and gates were sub-second. Root cause is the
sequential `for ticker in tickers` loop, not data or broker latency.

New tooling (not wired into the live judge path):
- `robinhood_tools/research_metrics.LatencyRecorder` — monotonic durations,
  UTC stamps, median/P95 and per-phase totals.
- `bounded_map` — bounded worker pool (cap 8) with per-item deadline,
  cooperative cancellation, explicit `timed_out`/`skipped` accounting and a
  result snapshot that late workers cannot mutate.
- `deterministic_prefilter` — cheap history/quote/spread/regime/session gates
  before any AI call, reusing existing gate semantics without weakening them.
- `EvidenceCache` — source, as-of, expiry, version, instrument; stale evidence
  is evicted and never reused for execution.
- `rate_limit_backoff` — bounded exponential backoff.

Lab microbenchmark (synthetic 9 × 50 ms tasks): sequential **0.453 s**,
bounded(3) **0.153 s** (~3×). This is *not* a claim about live NIM latency.
Live integration is deliberately deferred to a champion/challenger trial so
research evidence requirements are not weakened.

## E. Short-entry integration

`scripts/short_candidate_router.py` maps bearish research (`Sell`,
`Underweight`) to an independent short-setup evaluation. Key properties:
- `automatic_short` is pinned **False**; a research verdict never becomes an
  order.
- Explicit `position_effect` mapping: sell/open = new short, sell/close = long
  exit, buy/close = cover, buy/open = new long.
- Read-only adapter binds only `_validate_setup`, `fetch_short_eligibility`
  and `PortfolioRiskController.check()` — no order, ledger, state-machine or
  authorization derivation functions.
- Every record carries `shadow_only: true`, `orders_submitted: 0`,
  `enforcement_enabled: false` and `entry_permission: none`.

Live-artifact run at 12:25 ET: **13 bearish research decisions routed, 0
shadow candidates**; 5 rejected for missing independent setup, 8 rejected by
the technical validator. All fail closed with structured gate codes. The
12:25 cycle had overwritten the 10:45 short setups in
`outputs/daytrade/2026-11-24.json` (now 8 `long_reclaim` + 1 `range`), which is
itself an artifact-retention finding.

## F. RISK_OFF

Documented current policy: new equity buys and new option premium blocked
desk-wide; equity sells remain exits; options-mode bearish is a long put under
RISK_OFF; protective exits, emergency flatten and overnight exits remain
operational; the regime is recomputed per run and auto-recovers. There is no
existing short entry permitted under RISK_OFF.

New `scripts/risk_off_short_challenger.py` is shadow-only: chronological 60/40
walk-forward split, costs (5 bps round trip) and borrow (2.5 bps/day),
expectancy, win rate, drawdown and a Sharpe-like ratio by regime. With fewer
than 30 OOS samples it reports **insufficient evidence and no promotion
request**. RISK_OFF was not changed and no challenger was promoted.

## G. VIX evidence

`robinhood_tools/vix_evidence.VixEvidence` records value, source, retrieved_at
(UTC), market_as_of (from the discarded bar index), as-of source, timezone,
freshness, cache status, policy threshold and data version. `validate_evidence`
recomputes freshness from timestamps every call — a caller-supplied `freshness`
label is never trusted — and rejects missing as-of, stale, future or malformed
observations. The 25.0 threshold and the live gate callers are unchanged;
boundary tests confirm 25.0 passes and 25.01 blocks.

## H. November 24 end-of-day review (INCOMPLETE)

At 12:30 ET the five afternoon tasks have not run. `scripts/eod_lifecycle_report.py`
produced `outputs/reports/daily/2026-11-24-eod-lifecycle.json` with all five
events **Not verified** (last runs dated 11/23; flatten log 0 bytes from
10/30; previous 15:40 run failed with result 1). Read-only broker state at
12:14 ET: account ACTIVE, **0 positions, 0 open orders, 0 filled orders today**,
market open until 16:00. Zero morning orders do **not** establish zero orders
for the full session; re-run after 16:30 ET.

## I. Dashboard

New read-only `Intraday Decision Pipeline` section on `/trading` with
`/api/intraday-decision-pipeline`, backed by
`scripts/intraday_pipeline.py`. It shows scan cycle, symbols, long/short
candidates, confirmed setups, research completions/timeouts, latency fields
(marked not instrumented in the live path), risk-approved/blocked, shadow,
authorized/submitted/fills/positions, and each candidate's decision path such
as `Screened → Bearish Research → Short Setup → Shadow`. Policy rejections use
amber tags, technical failures red. Unverified counts are labelled
"not verified" rather than shown as zero.

## J. Tests

- New: `tests/test_phase8_1_engineering.py` (28) and
  `tests/test_phase8_1_ops_dashboard.py` (11) — 39 tests covering all 27
  requested categories where the environment permits.
- Full suite **1,228 OK, 2 skipped** (baseline 1,189 + 39).
- Ruff clean; `mypy robinhood_tools` clean (86 files); secret scan clean;
  execution-bypass guard PASS; certification harness **67 PASS / 0 FAIL / 2 SKIP**.
- Source-string tests are supplemented with behavioral tests; where a live
  integration does not exist (LLM concurrency, EOD execution), the report says
  so rather than claiming a pass.

## K. Adversarial review

Independent review found three HIGH findings, all fixed and re-tested:
1. `bounded_map` leaked daemon threads and mutated its result after return —
   replaced with a bounded executor, timeout snapshot and explicit accounting.
2. The shadow router's read-only adapter had wrong call signatures and an
   uncaught `.get()` on a risk dataclass, plus unsafe position handling —
   fixed with broker transport injection, result normalization and dict-aware
   position quantities.
3. `validate_evidence` trusted a caller-supplied freshness label — now
   recomputes from timestamps and checks retrieval age.

MEDIUM fixes: no caller-event mutation, UTC-normalized cache comparison,
injected-clock session completeness, no fabricated dashboard zeros, and
verifier-safe safety markers. Rejected shadow records no longer retain an
actionable plan.

## L. Git changes

Branch `feat/phase8-1-longshort` from `bf3e735`, commit and PR to be recorded
after CI. New/changed: `robinhood_tools/{regime_compat,research_metrics,vix_evidence}.py`,
`scripts/{short_candidate_router,risk_off_short_challenger,eod_lifecycle_report,intraday_pipeline}.py`,
`scripts/agent_office.py`, `web/agent-office/trading.html`,
`scripts/tradingagents_paper_auto.py` (import only), two new test files, and
this report. Owner's staged/untracked files preserved.

## M. Remaining risks

1. EOD lifecycle INCOMPLETE; re-run after 16:30 ET.
2. Live research latency not yet reduced — tooling exists but is not wired in.
3. Owner's staged regime fix still uncommitted; clean checkout relies on the
   shim until it is committed.
4. Shadow short routing produced zero candidates today; no evidence to support
   a RISK_OFF policy change.
5. `outputs/daytrade/<date>.json` is overwritten each cycle, losing earlier
   setups; per-cycle archival is not implemented.
6. VIX provenance module is not wired into the operational option gate.
7. Windows ACL and real broker rehearsal remain NOT TESTED from Phase 7.4.

## N. Final readiness verdict

**PHASE 8.1 VERIFIED WITH RESTRICTIONS**

Engineering fixes, shadow routing, provenance tooling, EOD verification and
dashboard visibility are implemented and tested. Live short routing, any
RISK_OFF policy change, VIX gate rewiring and research-latency integration
remain behind further validation and explicit owner approval. No new
unattended trading authorization is claimed; enforcement stays OFF.
