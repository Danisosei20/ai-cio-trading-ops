# Phase 8.4 — Intraday Signal Generation & Research Optimization

Prepared **16:36 ET 2026-11-24 after market close**. ADR 0001/0002 preserved;
authorization enforcement OFF; no live or paper order submitted.

## A. Executive summary

Added a canonical long/short signal factory with strict point-in-time evidence,
a hash-chained append-only signal archive, a deterministic market-data
capability manager, observational opportunity scoring, and a measured
partial-pipeline research benchmark. Independent adversarial review found
**6 HIGH** defects in the new code (dishonest live label, time-locked benchmark,
recorder key collision, masked overnight failures, self-certifying archive,
evidence-hash collisions) — all fixed and re-verified. No gate was weakened,
no challenger promoted, no order placed.

## B. Verified repository baseline

HEAD `443c1c3` (PR #79), 1,309 tests OK / 2 skipped, cert 67 PASS, CI green.
The owner's 10 staged files and all untracked work in the main checkout were
untouched. Worktree `phase8-4-signal-pipeline` used throughout.

## C. Long/short signal-generation architecture

Signal lineage: market bars → `bar_validation.validate_bars` →
`daytrade_scout` classification → `intraday_signal_factory` → research →
deterministic validation (`daytrade_engine._validate_setup`) → risk controller →
authorization/execution. Audit confirmed existing defects: scout overwrites
`outputs/daytrade/<date>.json` each cycle, two incompatible `short_breakdown`
definitions exist (live `price <= or_high` vs strict `price < or_low`),
`bar_validation` was not wired into the live path, `data_age_sec=0` was
hardcoded, ghost `SIGNAL_FOUND` trades consume dedup, relative-strength and
opportunity scoring were absent. The new factory/archive are **shadow
infrastructure**; live thresholds and routing were not altered.

## D. Short-breakdown validation findings

The canonical `short_breakdown` requires price **below VWAP and below the
opening-range low**, with optional relative weakness vs SPY; `long_reclaim` is
its exact mirror. A 16:36 ET run on real 5-minute yfinance bars for six symbols
produced **6 candidates, 0 validated, 6 rejected** — all "bars are stale for
this decision" after market close. This is correct fail-closed behavior: no
stale bar generated a tradeable short. The discrepancy with the looser live
scout rule is documented, not silently changed.

## E. Point-in-time evidence integrity

`intraday_signal_factory` emits unique signal IDs and evidence hashes that
include symbol, bars, setup, as-of, timeframe, source, feed, relative strength,
evidence refs and strategy version. `signal_archive` stores source bars,
technical values, classification, strategy version, rejection reasons, research
link, `evidence_sha256` and a `prev_chain_sha256`/`chain_sha256` link. Tampering
or recomputing a fabricated hash breaks verification; replay checks integrity.
Future-dated, out-of-order, stale and gapped bars are rejected before archival.

## F. Alpaca feed capabilities

`data_capability` maps each strategy to required capabilities and classifies
SUPPORTED / LIMITED / UNSUPPORTED / STALE / UNAVAILABLE. Real-time SIP,
consolidated volume and consolidated breadth are `UNSUPPORTED`/`UNAVAILABLE`
under the current entitlement; `consolidated_breadth` requires SIP and is
explicitly listed as requiring owner action. IEX volume is never presented as
consolidated; delayed SIP is never substituted for live execution. No paid
subscription was activated and no SIP endpoint was retried by this layer.

## G. Full research graph latency measurements

`research_pipeline_benchmark.py` measured seven phases on equivalent inputs with
a **real NVIDIA NIM inference phase** and measured local screening/validation.
Mode: `live_nim_partial_pipeline` — explicitly **not** the full TradingAgents
research graph. Latest run (6 symbols, 3 workers, after fixing recorder
identity and time-locked bars): champion wall **1.497 s**, challenger wall
**0.505 s**, **2.964×**; 6/6 completions both paths. Tool-call and news phases
are marked `not_run_in_partial_pipeline` and are not claimed as measured.
`nim_inference` totals 1.500 s sequential vs 1.437 s parallel (bounded by the
provider). Promotion remains disabled.

## H. Champion/challenger comparison

Sequential champion remains authoritative. The challenger writes only its own
shadow artifacts; no champion file is modified. Promotion requires multi-session
evaluation and owner approval; none is granted.

## I. Opportunity quality scoring

`opportunity_scoring` scores only verified boolean factors; missing factors
score zero, and a rejected candidate scores 0 regardless of supplied factors.
`out_of_sample_sufficient` requires >=5 sessions, >=50 candidates **and** a
computed expectancy — none of which holds today. Scores cannot change position
size, override a risk rejection, or authorize an entry.

## J. RISK_OFF short research

The desk-wide RISK_OFF restriction is unchanged. `risk_off_short_challenger`
remains shadow-only, with no execution permission. No bearish research was
converted into a short order.

## K. Canonical regime migration status

`docs/notes/regime-canonical-migration.md` still documents the exact canonical
patch. The owner's staged `market_prices.py` and `tests/test_market_prices.py`
were not modified; `regime_compat` keeps clean checkouts correct and both
signatures are tested. No threshold changed.

## L. November 24 end-of-day verification

| Event | Scheduled | Status | Evidence |
|---|---|---|---|
| Options watch | 15:10 | `INCOMPLETE` (ran; content-date check unmet) | scheduler result 0; `options-watch.log` |
| Overnight dry-run | 15:40 | `FAILED` | scheduler result 1; fixed exit-code bug; 0 entries |
| Intraday flatten | 15:45 | `INCOMPLETE` | scheduler result 0 but empty stale `flatten.log`; internal flatten at 15:17 rc=0 |
| News readout | 16:10 | `VERIFIED` | scheduler result 0; `news/explanations/2026-11-24.json` |
| Shadow scoring | 16:30 | `INCOMPLETE` | scheduler result 0; artifact content-date check unmet |

Market was closed at 16:36. Read-only broker: equity 84,247.28, **0 positions,
0 open orders, 0 filled orders today**; ledger `order_fills` has no 11/24 rows.
This is a full-session broker observation, not an inference from a midday
snapshot. `system_controls.emergency_kill = off`.

## M. Dashboard improvements

New read-only `Intraday Signal Intelligence` section and
`/api/signal-intelligence`: valid long/short setups, rejection reasons, archived
signal count (verified only), feed/volume basis, SIP-requiring strategies,
champion/challenger wall and median/P95, benchmark mode and timestamp, score
count and OOS sufficiency, short-lineage counts, and EOD status. Broker fields
render `not verified` rather than fabricated zeros. No `do_POST`; shadow
results are never realized P/L.

## N. Automated testing

27 new tests; full suite **1,336 OK, 2 skipped** (baseline 1,309 + 27).
Ruff clean; `mypy robinhood_tools` clean (90 files); secret scan clean;
execution-bypass guard PASS; certification harness 67 PASS / 0 FAIL / 2 SKIP.

## O. Independent adversarial review

6 HIGH findings (dishonest live label, time-locked bars, recorder identity
collision, masked overnight failures, self-certifying archive, evidence-hash
collisions) and key MEDIUM findings were fixed and independently re-verified
PASS. Residual caveats: `nim_live_benchmark` internal challenger mode remains
`nim` on total failure although the published mode is correctly
`laboratory_only_unavailable`; `LatencyRecorder` uniqueness relies on caller
task-id prefixes; overnight exit-branch failures still return 0 in the exit
path. No CRITICAL finding and no unresolved HIGH execution or evidence issue.

## P. Git changes and PR status

Branch `feat/phase8-4-signal-pipeline` from `443c1c3`; commit/PR to be recorded
after push and CI. New modules: `intraday_signal_factory.py`, `signal_archive.py`,
`opportunity_scoring.py`, `research_pipeline_benchmark.py`, `signal_intelligence.py`,
`robinhood_tools/data_capability.py`, two test files and this report. Modified:
`robinhood_tools/{bar_validation,research_metrics}.py`,
`scripts/{overnight_pilot,agent_office}.py`, `web/agent-office/trading.html`.

## Q. Remaining risks

1. EOD three events remain INCOMPLETE on content-date evidence despite result 0.
2. Full TradingAgents research graph latency still unmeasured (partial pipeline only).
3. Live scout still uses the looser short rule; strict factory is shadow-only.
4. Signal-time bar artifacts overwritten each cycle; replay relies on archives.
5. Real-time SIP unentitled; consolidated breadth unsupported.
6. Owner's staged regime fix still uncommitted.
7. No out-of-sample expectancy for the observational score or short challenger.

## R. Final verdict

**PHASE 8.4 VERIFIED WITH RESTRICTIONS**

Signal generation, point-in-time evidence, capability classification and
measured research acceleration are implemented and tested. Execution remains
behind existing deterministic gates and authorization; no promotion, no new
permission, and no order was submitted. This verdict is **not** unattended
trading certification.
