# Phase 8.5 — Signal Reliability, Immutable Evidence & Research Performance

Prepared **18:27 ET 2026-11-24 after market close**. ADR 0001/0002 preserved;
authorization enforcement OFF; no live or paper order submitted.

## A. Executive summary

Added immutable per-cycle market snapshots with **keyed HMAC** hashes, explicit
record/path identity binding and atomic exclusive creation; time-aware freshness
that distinguishes live eligibility from replay validity using an explicit
2026 exchange calendar; and a scout-vs-factory rule comparison. A bounded
**full TradingAgents graph** measurement was attempted with a 420 s hard
timeout and produced **no artifact** — full-graph latency is therefore
**NOT VERIFIED**, not estimated. Independent adversarial review found 6 HIGH
and several MEDIUM defects in the new code; all were fixed and re-verified.
No gate weakened, no promotion, no order.

## B. Repository baseline

HEAD `346a857` (PR #80), 1,336 tests OK / 2 skipped, cert 67 PASS, CI green.
Owner's staged/untracked files in the main checkout untouched. Worktree
`phase8-5-evidence` used throughout.

## C. Immutable snapshot implementation

`robinhood_tools/market_snapshot.py` stores
`outputs/market_snapshots/<date>/<cycle_id>/<symbol>/snapshot.json` with bars,
provider, feed, timeframe, market timestamps, retrieval timestamp, indicator
values, strategy version, signal ID and an HMAC-SHA256 `evidence_sha256`.
Key source: `CIO_SNAPSHOT_HMAC_KEY` or a private local key file
(`~/.cio/snapshot_hmac.key`), never written into artifacts. Final files use
`O_CREAT|O_EXCL` so duplicate cycle writes fail atomically; symlinked paths are
rejected; verification binds the record to its path. Replay sorts deterministically
and flags any integrity failure. Two real cycles × three symbols produced **6
verified keyed snapshots**; earlier unkeyed test snapshots are no longer
accepted as verified.

## D. Point-in-time signal freshness

`robinhood_tools/signal_freshness.py` evaluates live, replay and post-close
contexts. Naive timestamps are interpreted as exchange-local ET (never UTC);
outside-2026 dates and invalid dates fail closed as `unknown`; 2026 holidays,
weekends and early closes are explicit; future-dated bars, out-of-session
timestamps, missing/incomplete bars and unsupported feeds/capabilities are
rejected. Live freshness uses a configurable window; replay validity uses the
original retrieval-to-as-of gap and **never** grants live eligibility.

## E. Historical replay verification

Replay reads only archived snapshots and verifies each HMAC and path identity
before exposing bars. A tampered snapshot, a recomputed hash or a copy to
another cycle/symbol is rejected. Post-close analysis is labelled
`post_close`, not a current order path.

## F. Live scout vs canonical factory comparison

`scripts/scout_factory_compare.py` applies both rule sets to identical inputs.
On real post-close bars, **6 rows** were compared: **0 agreement,
6 implementation defects** — the live scout labelled `long_reclaim` while the
canonical rule rejected ("price not above opening-range high"). The comparison
is setup-rule only (no bar validation/odds/risk), is shadow-only, and changed
no live routing. Promotion would need replay, out-of-sample evidence and owner
approval.

## G. Full TradingAgents research latency

A real single-symbol full-graph run (`NVDA`, 2026-11-24, NIM, `market` analyst,
1 tool round, isolated results/cache/memory, 420 s hard timeout) **timed out
with no measurement artifact**. Therefore full-graph latency is
**NOT VERIFIED**. The only latency numbers in this phase remain the earlier
partial-pipeline benchmark (1.497 s → 0.505 s, 2.964×), which is explicitly
**not** a full-graph result. Observed historical champion research per symbol
was 232–443 s with two analysts and four tool rounds, but that is log evidence,
not a controlled full-graph measurement. The dashboard marks the full-graph
panel `not verified` when an artifact is missing, contains an error, or lacks a
numeric elapsed time.

## H. Champion/challenger comparison

Sequential champion remains authoritative; challenger artifacts are isolated
under `outputs/shadow/research/`. No automatic promotion and no challenger
order path.

## I. Short-setup research results

Strict short validation remains shadow-only. No `Sell`/`Underweight` became a
short order; RISK_OFF and VIX gates unchanged. The scout/factory discrepancies
above are research findings, not permission to change thresholds.

## J. Market-data capabilities

IEX real-time remains available; real-time SIP, consolidated volume and
consolidated breadth remain unsupported under the current entitlement. The
capability manager blocks strategies requiring those capabilities and does not
substitute delayed SIP or infer consolidated volume from IEX.

## K. November 24 scheduler / EOD verification

| Event | Scheduled | Task result | Status (artifact lifecycle) | Auditor full-file review |
|---|---|---|---|---|
| Options watch | 15:10 | 0 | INCOMPLETE (content-date check) | VERIFIED — log `done 15:10:25.55` |
| Overnight dry-run | 15:40 | 1 | FAILED | FAILED — logic bug; fix verified by controlled rerun (0/1/2 exits) |
| Intraday flatten | 15:45 | 0 but no artifact | INCOMPLETE | FAILED — false success, no execution evidence |
| News readout | 16:10 | 0 | VERIFIED | VERIFIED — `news/explanations/2026-11-24.json` |
| Shadow scoring | 16:30 | 0 | INCOMPLETE (content-date check) | VERIFIED — settle/score logs 16:30:20/28 |
| Intraday loop wrapper | 10:00 | 255 | FAILED | app work completed 15:17; batch markers absent |

Post-close broker (17:46 ET): equity **84,247.28**, **0 positions, 0 open
orders, 0 fills dated 11/24**; ledger `order_fills` 0 today. Audit events 2
today (fill reconciliation 09:45 and 15:10, both `ok`). The full-day zero-order
claim is broker-evidenced, not inferred from a midday snapshot. Overnight
entry remained `--dry-run`; no permission changed.

## L. Regime migration status

`docs/notes/regime-canonical-migration.md` still documents the exact canonical
patch. Owner's staged `market_prices.py` / `tests/test_market_prices.py`
untouched; `regime_compat` keeps clean checkouts working. No threshold changed.

## M. Dashboard changes

The read-only `/trading` Signal Intelligence panel now includes immutable
snapshot count (verified only), full-graph status/scope/elapsed, and
scout/factory agreement vs defect counts. Broker fields render `not verified`
rather than fabricated zeros; no `do_POST`, no XSS, loopback-only.

## N. Automated testing

23 new tests; full suite **1,359 OK, 2 skipped**. Ruff clean;
`mypy robinhood_tools` clean (92 files); secret scan clean; execution-bypass
guard PASS; certification harness 67 PASS / 0 FAIL / 2 SKIP.

## O. Independent adversarial review

6 HIGH findings (naive ET timestamps, 2027+ holiday fail-open, unkeyed/recomputable
snapshot hash, duplicate-write TOCTOU, fail-open bar defaults, ignored feed/timezone)
and MEDIUM findings were fixed and re-verified: keyed HMAC with path binding and
`O_EXCL` writes, exchange-local naive parsing with explicit calendar, fail-closed
defaults, feed/timezone validation, honest full-graph status. Residual documented
limitations: the HMAC key file must be protected by the host; `timezone_name` is
validated but session math uses ET; the viewer's pre-existing revocation read path
performs a DDL write; a few assertions remain plumbing checks rather than behavioral
proofs.

## P. Git changes and PR status

Branch `feat/phase8-5-evidence` from `346a857`; commit/PR recorded after push
and CI. New: `robinhood_tools/{market_snapshot,signal_freshness}.py`,
`scripts/scout_factory_compare.py`, two test files and this report. Modified:
`scripts/signal_intelligence.py`, `scripts/agent_office.py`,
`web/agent-office/trading.html`, and one time-dependent test in
`tests/test_phase8_1_engineering.py` (pinned clock, not weakened).

## Q. Remaining risks

1. Full TradingAgents graph latency NOT VERIFIED (420 s timeout, no artifact).
2. Live scout and canonical factory still disagree; factory remains shadow-only.
3. EOD artifact content-date checks disagree with full-file auditor review for
   options watch and shadow scoring; scheduler Operational log is disabled.
4. Intraday-loop batch wrapper exited 255 and 15:45 flatten left no evidence.
5. Real-time SIP still unentitled; consolidated breadth unsupported.
6. Owner's staged regime fix still uncommitted.
7. HMAC key protection is a host responsibility.

## R. Final readiness verdict

**PHASE 8.5 VERIFIED WITH RESTRICTIONS**

Signal evidence is now immutable and replay-verifiable, freshness is
time-context aware, diagnostics are honest about unverified measurements, and
all execution controls remain authoritative. No new permission, no promotion,
no order, and no unattended-trading certification is granted by this verdict.
