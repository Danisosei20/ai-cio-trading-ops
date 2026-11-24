# Phase 8.3 — Market Data Recovery, Short-Signal Integrity & Live NVIDIA Benchmark

Prepared **14:1x ET 2026-11-24 while the market was open**. Afternoon EOD tasks
were not due and are reported INCOMPLETE. Paper only; enforcement OFF; no live
or paper order submitted. ADR 0001/0002 preserved.

## A. Executive summary

Root-caused the Alpaca HTTP 403 precisely: **missing real-time SIP entitlement**
on an otherwise valid, entitled paper key. IEX real-time, historical SIP,
delayed SIP and news/options endpoints all return 200. Hardened feed handling,
added structured 403 classification and bar/lineage validation that fail closed
on future data, invalid OHLCV, gaps and missing identity. Benchmarked the shadow
challenger against **real NVIDIA NIM requests**: 6 symbols, 3 workers,
**1.699 s → 0.947 s (1.794×)** with 0 timeouts, explicitly labelled provider
latency (not the full research graph, not a promotion signal). Independent
adversarial review found 4 HIGH fail-open defects in the new validators; all
were fixed and re-verified.

## B. Repository baseline and preservation

- HEAD `0a848b2` (PR #78 merged), baseline 1,270 tests OK / 2 skipped, CI green.
- Main checkout retains the owner's 10 staged files (including
  `robinhood_tools/market_prices.py` and `tests/test_market_prices.py`) and all
  untracked work. None were modified or staged here. Worktree
  `phase8-3-market-data` used throughout.

## C. Alpaca 403 root cause

Actual read-only GETs (14:06–14:07 ET, same authenticated paper key):
- `paper-api.alpaca.markets/v2/account` → **200** (ACTIVE, options level 3).
- `data.alpaca.markets/v2/stocks/QQQ/snapshot?feed=iex` → **200**; `?feed=sip` →
  **403** with JSON `{"message":"subscription does not permit querying recent SIP data"}`.
- `/trades/latest` and `/quotes/latest` → 200 on IEX, **403** on SIP; no-feed
  variants return `X-Feed: iex`.
- Historical SIP (`bars`, `trades`, `quotes` with start/end) → **200**;
  `feed=delayed_sip` snapshot/latest/trades/quotes → **200** (~15 min delayed,
  `X-Feed` consolidated); `feed=delayed_sip` on `/bars` → **400 invalid feed**.

Classification: **real-time SIP subscription not entitled**. Invalid key,
paper/data mismatch, wrong URL and rate limiting were ruled out by evidence.
The repo defaults to IEX everywhere, so no normal path requests SIP; the 403
originates from explicit SIP calls (probes or SIP WebSocket). Owner action:
enable real-time SIP in the Alpaca dashboard if consolidated real-time data is
needed; otherwise stay on IEX, or use `delayed_sip` only for latest endpoints
with explicit delay labeling. No permission bypass was attempted.

Implementation: `robinhood_tools/market_data_health.py` classifies failures from
status **and** provider body (`entitlement_realtime_sip`, `entitlement_data_access`,
`unsupported_feed`, `authentication_or_permission`, `rate_limited`,
`provider_outage`, `connection_failure`); `alpaca_market_data._feed` now rejects
`delayed_sip` for bars; `_get` uses the configured base URL; `realtime_stream`
no longer silently maps unknown feeds to the paid SIP WebSocket.

## D. Market-data reliability and feed coverage

`robinhood_tools/bar_validation.py` validates completed, ordered, deduplicated,
gap-free bars; computes VWAP and opening range from those validated inputs only;
rejects future-dated bars, bars after session end, stale bars, malformed OHLCV,
non-dict rows and gaps. IEX volume is labelled `iex_only` and never presented as
consolidated market volume. `market_data_evidence.py` (Phase 8.2) requires source,
retrieval/as-of timestamps, cache status and freshness, recomputing freshness
rather than trusting labels. Reproducible health report:
`outputs/reports/diagnostics/2026-11-24-market-data-health.json`.

Limitation: an `HTTP 403` entitlement response is unavailable data, never a
substituted price. No third-party source was introduced as a workaround.

## E. Short-signal lineage results

`scripts/short_signal_lineage.py` traced all 16 daily-review rows (13 bearish,
3 bullish) against surviving artifacts:

| Outcome | Count |
|---|---|
| Eligible for independent short validation | **0** |
| Setup identity missing / mismatch | 5 |
| Setup direction mismatch (`long_reclaim` / `range`) | 7 |
| Setup timestamp after the signal (future data) | 1 |
| Not bearish research | 3 |

Signal-time short setups asserted by the 12:07 review could not be independently
verified from surviving bars: the current artifact is a 13:53 snapshot and
per-cycle archives are copies of the 12:25 (long-biased) snapshot. No setup was
relabelled. `Sell`/`Underweight` remained research inputs,
`automatic_short: false` on every row. Report:
`outputs/reports/short/2026-11-24-short-signal-lineage.json`.

## F. Short technical validation findings

`bar_validation.short_breakdown()` requires price below VWAP **and** below the
opening-range low, with optional relative-weakness confirmation; invalid setups
get specific reasons. All routing remains shadow-only via
`short_candidate_router` and `risk_off_short_challenger`; RISK_OFF and VIX
thresholds are unchanged. No candidate reached borrow, risk or broker stages.

## G. Real NVIDIA performance measurements

`scripts/nim_live_benchmark.py` issued **real** chat-completion requests to
`https://integrate.api.nvidia.com/v1/chat/completions` with model
`nvidia/nemotron-3.5-lightning-30b-a3b`, prompt `phase8.3-provider-latency-v1`,
8 max tokens, 6 symbols, 3 workers, per-symbol/ batch deadlines and a bounded
rate limiter. Latest run (`outputs/reports/research/2026-11-24-nim-live-benchmark.json`):

| Metric | Value |
|---|---|
| Mode | `live_nim` (HTTP 200 for 6/6) |
| Sequential wall | 1.699 s |
| Parallel wall | 0.947 s |
| Wall speedup | **1.794×** |
| Provider latency mean (sequential / parallel) | 0.283 s / 0.388 s |
| Parallel responses / timeouts | 6 / 0 |
| Promotion | `none - automatic promotion disabled` |

The parallel mean is higher because client-side rate limiting/queueing is
included; wall-clock figures include that overhead. This benchmark measures
**provider latency only**, not the full TradingAgents research graph, not
research conclusions, and not realized performance. No API key is written to
artifacts; `secret scan` is clean.

## H. Champion/challenger comparison

`champion_challenger.compare` normalizes decisions, retains raw decisions,
compares evidence keys, reports disagreements and latency distributions, and
hard-codes no promotion. Multi-session thresholds (>=5 sessions, >=50 symbols)
are not met by this single benchmark, so the recommendation remains
"insufficient evidence; keep champion". Live champion routing unchanged.

## I. Canonical market-regime migration status

`docs/notes/regime-canonical-migration.md` still documents the exact canonical
patch. The owner's staged `market_prices.py` / `tests/test_market_prices.py`
changes were not touched; `regime_compat` keeps clean checkouts correct and
both signatures are tested. No threshold changed. Integration remains an owner
action.

## J. November 24 end-of-day verification (INCOMPLETE)

At ~14:1x ET none of the five afternoon tasks were due: 15:10 options watch,
15:40 overnight dry-run, 15:45 flatten, 16:10 news and 16:30 shadow scoring —
all **NOT TESTED / INCOMPLETE** (last runs dated 11/23; flatten log 0 bytes;
prior 15:40 result 1). EOD lifecycle artifact:
`outputs/reports/daily/2026-11-24-eod-lifecycle.json`. Read-only broker check
at 14:09 ET: **0 positions, 0 open orders, 0 fills today**, clock open until
16:00 ET. This is a point-in-time observation, not a full-day zero-order claim.

## K. Dashboard changes

The read-only `/trading` Research Performance panel now shows market-data
entitlement/feed state, short-lineage mismatch/future/missing counts, and NIM
benchmark mode/speedup/responses/timeouts with a live-vs-laboratory label and
promotion status. Missing data renders as "unavailable", never zero; no
`do_POST`; shadow results are never shown as realized trades.

## L. Security and adversarial review

First review: 4 HIGH (future bars, invalid OHLCV/non-dict rows, future setup
without signal time, setup identity bypass) plus MEDIUM (naive timestamps,
unguarded gaps, 403 mislabel). All fixed: future/after-session/stale/gap checks,
`close` range validation, non-dict rejection, UTC-normalized timestamps,
required signal/setup timestamps, explicit setup symbol identity, tightened
entitlement text matching, and like-for-like provider latency fields. Focused
re-review confirmed every HIGH/MEDIUM item PASS. No order path, no promotion,
no key leakage; staged user files untouched.

## M. Automated test results

39 new tests across `tests/test_phase8_3_market_data.py` and
`tests/test_phase8_3_short_nim.py`; full suite **1,309 OK, 2 skipped**.
Ruff clean; `mypy robinhood_tools` clean (89 files); secret scan clean;
execution-bypass guard PASS; certification harness **67 PASS / 0 FAIL / 2 SKIP**.
CI workflow result recorded in section N.

## N. Git changes and PR status

Branch `feat/phase8-3-market-data` from `0a848b2`; commit/PR to be recorded
after push and CI. New: `robinhood_tools/{market_data_health,bar_validation}.py`,
`scripts/{short_signal_lineage,nim_live_benchmark}.py`, two test files and this
report. Modified: `robinhood_tools/{alpaca_market_data,realtime_stream,research_performance}.py`,
`scripts/research_performance.py`, `web/agent-office/trading.html`.

## O. Remaining risks

1. Real-time SIP remains unentitled; consolidated real-time breadth cannot be
   claimed. Delayed SIP is ~15 minutes old and invalid for bars.
2. EOD outcomes not yet available; re-run after 16:30 ET.
3. NIM benchmark measures provider latency, not full research quality; single
   run, no promotion.
4. Signal-time short bar artifacts were overwritten; lineage relied on surviving
   snapshots and never relabelled a setup.
5. Staged regime fix still uncommitted.
6. Windows ACL and real broker rehearsal remain NOT TESTED from Phase 7.4.

## P. Final verdict

**PHASE 8.3 VERIFIED WITH RESTRICTIONS**

Data failures are correctly diagnosed, short-signal lineage fails closed, real
NIM latency is measured honestly, and all execution controls remain
authoritative. No policy change, no promotion, no unattended activation, and
no order was submitted. This verdict does **not** authorize unattended trading.
