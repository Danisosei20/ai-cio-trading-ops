# Phase 8.2 — Research Acceleration, Long/Short Validation & Pipeline Optimization

Prepared **13:44 ET 2026-11-24 while the market was open**. Afternoon EOD tasks
were not due and are reported INCOMPLETE. Paper only; enforcement OFF;
no live or paper order submitted. ADR 0001/0002 preserved.

## A. Executive summary

Added a bounded, isolated **shadow parallel NVIDIA NIM research challenger**;
the sequential champion still owns all authoritative artifacts and gate verdicts.
Instrumented per-symbol latency, built a deterministic champion/challenger
comparison with **automatic promotion disabled**, diagnosed all 13 bearish
candidate rejections as **integration/evidence defects rather than validated
short-policy rejections**, fixed the short-router direction and equity defects,
added append-only setup archival and market-data provenance, and extended the
read-only dashboard. Independent adversarial review found 2 CRITICAL and
7 HIGH issues; all were fixed and re-verified.

## B. Repository baseline

- HEAD `ce0c565` (PR #77 merged), CI green; baseline 1,228 tests OK / 2 skipped.
- Main checkout still has the owner's 10 staged and many untracked files,
  including the staged `market_prices.py` fix. None were modified here.
- Worktree `phase8-2-research-accel` used throughout.

## C. Research architecture

Champion (`tradingagents_paper_auto.py`) remains sequential, authoritative, and
unchanged in routing. Challenger (`scripts/shadow_research_challenger.py`):
- max 3 workers by default, bounded queue, per-symbol and batch deadlines,
  cooperative cancellation, bounded retries on rate-limit/timeout only,
  exponential backoff with a hard cap, and a process-wide rate limiter;
- writes **only** `outputs/shadow/research/<run_id>/<SYMBOL>/challenger.json`
  plus `batch.json`; path traversal, champion-root paths and symlinked outputs
  are rejected before any write;
- no order/authorization imports; every artifact carries `shadow_only: true`,
  `orders_submitted: 0`, `entry_permission: none`, `enforcement_enabled: false`;
- incomplete evidence, provider errors and timeouts produce `UNKNOWN`, never a
  successful trading decision.

CLI defaults to laboratory mode (`--enable-nim` refuses without a real provider
callable). `scripts/champion_challenger.py` compares normalized decisions and
evidence keys, retains raw decisions, reports disagreements and hard-codes
`promotion_recommendation: "none - automatic promotion disabled"`.

## D. Research latency

Live champion log (11/24 10:45 batch): nine sequential researches, 232–443 s
each, ~55 minutes total. The challenger's live improvement is **NOT measured**.

Laboratory benchmark (`outputs/reports/research/benchmark-2026-11-24.json`,
synthetic fixed 50 ms provider, 9 symbols, prompt `phase8.2-v1`, 4 tool rounds,
3 workers, NIM model config recorded): **0.454 s sequential → 0.206 s parallel,
2.20× speedup**, 9/9 completed, 0 timeouts. This is a lab result and must not be
read as live NVIDIA NIM latency.

## E. Short candidate analysis (13 records)

Source: Phase 8.1 shadow report (12:36 ET) and live artifacts.

| Category | Count | Symbols |
|---|---|---|
| `missing_setup_or_equity` | 5 | A, APP, BBY, DLTR, SJM |
| `technical_setup_rejected` on mismatched `long_reclaim`/`range` setups | 8 | AAPL, AMD, AMZN, META, NVDA, QQQ, SPY, TSLA |
| Validated short-policy rejection | **0** | — |
| Borrow / risk / regime rejection reached | **0** | — |

Root causes: (1) the 12:25 scout cycle overwrote the 10:45 short setups in
`outputs/daytrade/<date>.json`; (2) the router validated any setup label without
checking it was `short_breakdown`; (3) the risk adapter passed `equity=None`.
These are **technical/integration defects**, not evidence that the desk's
VIX/RISK_OFF/odds rejections were wrong. The live desk rejected the same names
for VIX > 25 (5), desk-wide RISK_OFF (1), odds (3) and bearish-with-no-position
(4). No missed winner is claimed; no point-in-time entry/exit replay exists.

After fixes, a fresh routing run produced 11 bearish decisions: 5 missing setup
and 6 `setup_direction_mismatch`, 0 shadow candidates, 0 orders — correctly
fail-closed against the current (12:25) long-biased artifact.

## F. Short validator improvements

- Router now rejects non-`short_breakdown` setups **before** the short validator
  (`setup_direction_mismatch`).
- Risk checks now receive the exact equity value.
- `scripts/setup_archive.py` writes append-only per-cycle snapshots; wired into
  `daytrade_scout.py` with a failure-isolated call so live routing is unchanged.
- Shadow records now carry explicit `approved: false`.
- RISK_OFF, VIX, short eligibility and all execution permissions unchanged;
  `risk_off_short_challenger` remains shadow-only.

## G. Market-data quality

`robinhood_tools/market_data_evidence.py` records instrument, source, feed,
`volume_basis`, timeframe, retrieved/market-as-of UTC timestamps, cache status
and freshness; it recomputes freshness from timestamps and rejects forged labels,
stale bars and missing fields. IEX-only feeds are explicitly labelled **NOT
consolidated market volume**. Alpaca market-data GETs returned 403 (no data
entitlement) during verification, so today's scout evidence came from yfinance;
the code does not itself prove bar freshness and hardcodes `data_age_sec=0`.
These are documented limitations, not fulfilled evidence.

## H. Market-regime compatibility

`regime_compat` supports both signatures and was tested with both; clean
checkouts no longer turn a `TypeError` into `UNKNOWN`. The owner's staged fix
remains uncommitted and untouched. `docs/notes/regime-canonical-migration.md`
gives the exact canonical patch and owner action. No threshold changed.

## I. November 24 end-of-day verification (INCOMPLETE)

At 13:44 ET none of the five afternoon events were due: 15:10 options watch,
15:40 overnight dry-run, 15:45 flatten, 16:10 news, 16:30 shadow scoring —
all **INCOMPLETE / NOT TESTED** (last runs dated 11/23; flatten log 0 bytes from
10/30; previous 15:40 result 1). `eod_lifecycle_report.py` now emits VERIFIED /
FAILED / SKIPPED / NOT TESTED / INCOMPLETE with evidence paths and ET/UTC-aware
broker fields. Read-only broker check at 12:56 ET: account ACTIVE, equity
84,247.28, **0 positions, 0 open orders, 0 fills today**, clock open until 16:00.
Zero morning orders do **not** establish zero orders for the full session.

## J. Dashboard

New read-only `Research Performance & Short Opportunity Diagnostics` section on
`/trading` (`/api/research-performance`, `scripts/research_performance.py`):
champion median/P95 from live research-log timestamps, challenger lab metrics
labelled LABORATORY ONLY, rejection-reason counts, operational state and EOD
status. Missing data renders as `data unavailable`, never zero. No `do_POST`, no
shadow P/L as realized, no unfilled orders shown as positions.

## K. Automated testing

New: `tests/test_phase8_2_research.py` and `test_phase8_2_short_data.py` — **42
tests** covering all 26 requested categories where the environment permits.
Full suite **1,270 OK, 2 skipped** (baseline 1,228 + 42). Ruff clean;
`mypy robinhood_tools` clean (87 files); secret scan clean; execution-bypass
guard PASS; certification harness **67 PASS / 0 FAIL / 2 SKIP**. CI workflow run
for the PR is recorded in section M.

## L. Adversarial review

First review: 2 CRITICAL (symbol/path-traversal and symlink/champion-root
writes), 7 HIGH (cancellation, false wave timeouts, broken artifact glob,
champion-log regex, evidence truthiness, dropped evidence keys, stale freshness)
and several MEDIUM. All fixed: strict symbol/run_id/out_root validation and
`symlink` guards; named cancellation param verified; per-symbol deadline and
wave-scaled batch budget; corrected glob and real log regex; literal-boolean
evidence validation with `evidence_keys` propagation; timestamp-recomputed
freshness; EOD running-code and UTC-date fixes. A focused second review confirmed
the critical and high findings resolved; two residual items were fixed and
re-tested. No unresolved CRITICAL/HIGH issue remains.

## M. Git changes

Branch `feat/phase8-2-research-accel` from `ce0c565`; commit `pending` and PR
`pending` at report time (to be recorded after push/CI). Files: new
`scripts/{shadow_research_challenger,champion_challenger,research_performance,setup_archive}.py`,
new `robinhood_tools/market_data_evidence.py`, two new test files, the final
report and regime-migration note; modified `scripts/{agent_office,daytrade_scout,short_candidate_router,eod_lifecycle_report}.py`
and `web/agent-office/trading.html`, plus `robinhood_tools/research_metrics.py`.

## N. Remaining risks

1. EOD outcomes not yet available; re-run the verifier after 16:30 ET.
2. No live NVIDIA NIM challenger measurement; lab speedup only.
3. Alpaca data entitlement 403; market-data provenance incomplete in live artifacts.
4. Owner's staged regime fix still uncommitted.
5. Shadow short routing has zero executable candidates; no evidence supports a
   RISK_OFF policy change.
6. Windows ACL and real broker rehearsal remain NOT TESTED from Phase 7.4.

## O. Readiness verdict

**PHASE 8.2 VERIFIED WITH RESTRICTIONS**

Parallel shadow research, comparison tooling, validator fixes, diagnostics and
testing are implemented and verified. Live routing remains sequential, champion
artifacts remain authoritative, no challenger promotion is possible, RISK_OFF /
VIX / authorization / overnight / live permissions are unchanged, and no trade
was submitted. This verdict is **not** unattended trading certification.
